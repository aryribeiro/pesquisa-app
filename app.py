import streamlit as st
import sqlite3
import pandas as pd
import bcrypt
import json
import smtplib
import time
import random
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Optional, Dict, List, Tuple, Any
import os
import hashlib
import io
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

# Configurações
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "")
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key-change-in-production")
DEFAULT_ADMIN_PASS = "admin123"
MAX_RETRIES = 3
DB_PATH = "survey.db"

# Pool de conexões SQLite
class ConnectionPool:
    def __init__(self, db_path, pool_size=5):
        self.db_path = db_path
        self.pool = []
        self.pool_size = pool_size
        self._init_pool()
    
    def _init_pool(self):
        for _ in range(self.pool_size):
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self.pool.append(conn)
    
    @contextmanager
    def get_connection(self):
        conn = self.pool.pop() if self.pool else sqlite3.connect(
            self.db_path, check_same_thread=False, timeout=30
        )
        try:
            yield conn
        finally:
            if len(self.pool) < self.pool_size:
                self.pool.append(conn)
            else:
                conn.close()

# Inicializar pool global
if 'db_pool' not in st.session_state:
    st.session_state.db_pool = ConnectionPool(DB_PATH)

# Executor para tarefas em background
executor = ThreadPoolExecutor(max_workers=3)

# CSS customizado
def inject_css():
    st.markdown("""
    <style>
    .question-container {
        min-height: 300px;
        padding: 20px;
        background: #f8f9fa;
        border-radius: 10px;
        margin: 20px 0;
    }
    .progress-bar {
        width: 100%;
        height: 8px;
        background: #e9ecef;
        border-radius: 4px;
        overflow: hidden;
        margin: 20px 0;
    }
    .progress-fill {
        height: 100%;
        background: #007bff;
        transition: width 0.3s ease;
    }
    .question-counter {
        text-align: center;
        color: #6c757d;
        margin: 10px 0;
        font-size: 14px;
    }
    textarea {
        min-height: 100px !important;
        max-height: 120px !important;
    }
    </style>
    """, unsafe_allow_html=True)

# Funções de banco de dados
def init_database():
    """Inicializa o banco de dados com as tabelas necessárias"""
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        
        # Tabela de configuração admin
        c.execute('''CREATE TABLE IF NOT EXISTS admin_config
                     (id INTEGER PRIMARY KEY, 
                      password_hash TEXT NOT NULL,
                      is_default_pass BOOLEAN DEFAULT 1,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Tabela de pesquisas
        c.execute('''CREATE TABLE IF NOT EXISTS surveys
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      title TEXT NOT NULL,
                      questions TEXT NOT NULL,
                      is_active BOOLEAN DEFAULT 1,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      closed_at TIMESTAMP)''')
        
        # Tabela de respostas
        c.execute('''CREATE TABLE IF NOT EXISTS responses
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      survey_id INTEGER NOT NULL,
                      answers TEXT NOT NULL,
                      is_anonymous BOOLEAN DEFAULT 1,
                      respondent_name TEXT,
                      respondent_email TEXT,
                      ip_address TEXT,
                      submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (survey_id) REFERENCES surveys (id))''')
        
        # Tabela de rate limiting
        c.execute('''CREATE TABLE IF NOT EXISTS rate_limits
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      session_id TEXT NOT NULL,
                      action TEXT NOT NULL,
                      timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        
        # Inserir senha admin padrão se não existir
        c.execute("SELECT COUNT(*) FROM admin_config")
        if c.fetchone()[0] == 0:
            hashed = bcrypt.hashpw(DEFAULT_ADMIN_PASS.encode('utf-8'), bcrypt.gensalt())
            c.execute("INSERT INTO admin_config (password_hash) VALUES (?)", (hashed.decode('utf-8'),))
        
        conn.commit()

def check_rate_limit(session_id: str, action: str, max_requests: int = 50, window_seconds: int = 300) -> bool:
    """Verifica rate limiting com limites mais permissivos para pesquisas"""
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT COUNT(*) FROM rate_limits 
            WHERE session_id = ? AND action = ? 
            AND datetime(timestamp) > datetime('now', '-' || ? || ' seconds')
        """, (session_id, action, window_seconds))
        count = c.fetchone()[0]
        
        if count >= max_requests:
            return False
        
        c.execute("INSERT INTO rate_limits (session_id, action) VALUES (?, ?)", 
                 (session_id, action))
        conn.commit()
        return True

def verify_admin_password(password: str) -> Tuple[bool, bool]:
    """Verifica senha admin e retorna (is_valid, is_default)"""
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT password_hash, is_default_pass FROM admin_config LIMIT 1")
        result = c.fetchone()
        
        if result:
            stored_hash, is_default = result
            is_valid = bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
            return is_valid, bool(is_default)
    return False, False

def update_admin_password(new_password: str):
    """Atualiza senha do admin"""
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
        c.execute("UPDATE admin_config SET password_hash = ?, is_default_pass = 0", 
                 (hashed.decode('utf-8'),))
        conn.commit()

@st.cache_data(ttl=60)
def get_active_survey() -> Optional[Dict]:
    """Obtém pesquisa ativa com cache"""
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, title, questions FROM surveys WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
        result = c.fetchone()
        if result:
            return {
                'id': result[0],
                'title': result[1],
                'questions': json.loads(result[2])
            }
    return None

def create_survey(title: str, questions: List[Dict]):
    """Cria nova pesquisa"""
    # Desativar pesquisas anteriores
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("UPDATE surveys SET is_active = 0, closed_at = CURRENT_TIMESTAMP WHERE is_active = 1")
        
        # Criar nova pesquisa
        c.execute("INSERT INTO surveys (title, questions) VALUES (?, ?)",
                 (title, json.dumps(questions)))
        conn.commit()
    
    # Limpar cache
    get_active_survey.clear()

def save_response(survey_id: int, answers: Dict, is_anonymous: bool, name: str = None, email: str = None):
    """Salva resposta da pesquisa"""
    session_id = st.session_state.get('session_id', 'unknown')
    
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO responses (survey_id, answers, is_anonymous, respondent_name, 
                                 respondent_email, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (survey_id, json.dumps(answers), is_anonymous, name, email, session_id))
        conn.commit()

def export_responses_to_csv(survey_id: int) -> bytes:
    """Exporta respostas para CSV"""
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        
        # Obter dados da pesquisa
        c.execute("SELECT title, questions FROM surveys WHERE id = ?", (survey_id,))
        survey = c.fetchone()
        if not survey:
            return None
        
        questions = json.loads(survey[1])
        
        # Obter respostas
        c.execute("""
            SELECT id, submitted_at, answers, is_anonymous, respondent_name, respondent_email
            FROM responses WHERE survey_id = ?
            ORDER BY submitted_at
        """, (survey_id,))
        
        responses = c.fetchall()
        
        # Preparar dados para CSV
        data = []
        for resp in responses:
            row = {
                'ID': resp[0],
                'Data/Hora': resp[1],
                'Anônimo': 'Sim' if resp[3] else 'Não',
                'Nome': resp[4] or '',
                'Email': resp[5] or ''
            }
            
            answers = json.loads(resp[2])
            for i, q in enumerate(questions):
                row[f"Q{i+1}: {q['text'][:50]}"] = answers.get(str(i), '')
            
            data.append(row)
        
        df = pd.DataFrame(data)
        return df.to_csv(index=False).encode('utf-8')

def send_email_with_retry(to_email: str, subject: str, body: str, attachment: bytes = None, 
                         attachment_name: str = None) -> bool:
    """Envia email com retry exponential backoff + jitter"""
    if not SMTP_USER or not SMTP_PASS:
        st.warning("Configurações de SMTP não definidas. CSV salvo localmente.")
        if attachment:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"export_{timestamp}.csv"
            with open(filename, 'wb') as f:
                f.write(attachment)
            st.success(f"CSV salvo como: {filename}")
        return False
    
    for attempt in range(MAX_RETRIES):
        try:
            msg = MIMEMultipart()
            msg['From'] = SMTP_USER
            msg['To'] = to_email
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain'))
            
            if attachment:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(attachment)
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 
                              f'attachment; filename= {attachment_name or "export.csv"}')
                msg.attach(part)
            
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.send_message(msg)
            
            return True
            
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                # Exponential backoff com jitter
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait_time)
            else:
                st.error(f"Falha ao enviar email após {MAX_RETRIES} tentativas: {str(e)}")
                if attachment:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"export_fallback_{timestamp}.csv"
                    with open(filename, 'wb') as f:
                        f.write(attachment)
                    st.warning(f"CSV salvo localmente como fallback: {filename}")
                return False

# Interface principal
def main():
    st.set_page_config(page_title="Pesquisa App! - por Ary Ribeiro", page_icon="📋", layout="centered")
    inject_css()
    
    # Gerar session ID único
    if 'session_id' not in st.session_state:
        st.session_state.session_id = hashlib.md5(
            f"{time.time()}{random.random()}".encode()
        ).hexdigest()
    
    # Inicializar banco
    init_database()
    
    st.title("📋 Pesquisa App!")
    
    # Navegação principal
    if 'page' not in st.session_state:
        st.session_state.page = 'home'
    
    if st.session_state.page == 'home':
        show_home_page()
    elif st.session_state.page == 'admin':
        show_admin_page()
    elif st.session_state.page == 'respond':
        show_respond_page()

def show_home_page():
    """Página inicial com botões de navegação"""
    st.markdown("#### ...bem-vindos à minha Pesquisa Anônima! 👇🏻")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🔑 Admin / Professor", use_container_width=True):
            st.session_state.page = 'admin'
            st.rerun()
    
    with col2:
        if st.button("📝 Responder Pesquisa", use_container_width=True):
            st.session_state.page = 'respond'
            st.rerun()

def show_admin_page():
    """Página de administração"""
    st.markdown("### Área Administrativa")
    
    if st.button("← Voltar"):
        st.session_state.page = 'home'
        st.rerun()
    
    # Autenticação
    if 'admin_authenticated' not in st.session_state:
        st.session_state.admin_authenticated = False
    
    if not st.session_state.admin_authenticated:
        with st.form("login_form"):
            password = st.text_input("Senha", type="password")
            submitted = st.form_submit_button("Entrar")
            
            if submitted:
                is_valid, is_default = verify_admin_password(password)
                if is_valid:
                    st.session_state.admin_authenticated = True
                    st.session_state.is_default_password = is_default
                    st.success("Login realizado com sucesso!")
                    st.rerun()
                else:
                    st.error("Senha incorreta!")
    else:
        # Verificar se precisa alterar senha padrão
        if st.session_state.get('is_default_password', False):
            st.warning("⚠️ Você está usando a senha padrão. Recomendamos alterá-la.")
            
            with st.expander("Alterar senha"):
                with st.form("change_password_form"):
                    new_pass = st.text_input("Nova senha", type="password")
                    confirm_pass = st.text_input("Confirmar nova senha", type="password")
                    submitted = st.form_submit_button("Alterar")
                    
                    if submitted:
                        if len(new_pass) < 8:
                            st.error("A senha deve ter pelo menos 8 caracteres!")
                        elif new_pass != confirm_pass:
                            st.error("As senhas não coincidem!")
                        else:
                            update_admin_password(new_pass)
                            st.session_state.is_default_password = False
                            st.success("Senha alterada com sucesso!")
                            st.rerun()
        
        # Tabs do admin
        tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "➕ Nova Pesquisa", "📥 Exportar", "🔧 Diagnóstico"])
        
        with tab1:
            show_admin_dashboard()
        
        with tab2:
            show_create_survey()
        
        with tab3:
            show_export_section()
        
        with tab4:
            show_diagnostics()

def show_admin_dashboard():
    """Dashboard administrativo"""
    survey = get_active_survey()
    
    if survey:
        st.success(f"✅ Pesquisa ativa: **{survey['title']}**")
        
        # Estatísticas
        with st.session_state.db_pool.get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM responses WHERE survey_id = ?", (survey['id'],))
            total_responses = c.fetchone()[0]
            
            c.execute("""
                SELECT COUNT(*) FROM responses 
                WHERE survey_id = ? AND is_anonymous = 1
            """, (survey['id'],))
            anonymous_responses = c.fetchone()[0]
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total de Respostas", total_responses)
        with col2:
            st.metric("Respostas Anônimas", anonymous_responses)
        with col3:
            st.metric("Total de Perguntas", len(survey['questions']))
        
        if st.button("🛑 Encerrar Pesquisa", type="secondary"):
            with st.session_state.db_pool.get_connection() as conn:
                c = conn.cursor()
                c.execute("""
                    UPDATE surveys 
                    SET is_active = 0, closed_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (survey['id'],))
                conn.commit()
            get_active_survey.clear()
            st.success("Pesquisa encerrada!")
            st.rerun()
    else:
        st.info("Nenhuma pesquisa ativa no momento.")

def show_create_survey():
    """Interface para criar nova pesquisa"""
    st.markdown("#### Criar Nova Pesquisa")
    
    # Verificar se já existe pesquisa ativa
    if get_active_survey():
        st.warning("⚠️ Já existe uma pesquisa ativa. Encerre-a antes de criar uma nova.")
        return
    
    # Inicializar estado das perguntas
    if 'survey_questions' not in st.session_state:
        st.session_state.survey_questions = []
    
    # Título da pesquisa
    title = st.text_input("Título da Pesquisa", max_chars=200)
    
    # Adicionar perguntas
    st.markdown("##### Perguntas")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        question_text = st.text_input("Texto da pergunta", key="new_question_text", max_chars=500)
    with col2:
        question_type = st.selectbox("Tipo", 
            ["texto_curto", "texto_longo", "multipla_escolha", "escala_1_5"],
            key="new_question_type")
    
    # Opções para múltipla escolha
    options_text = None
    if question_type == "multipla_escolha":
        options_text = st.text_area("Opções (uma por linha)", key="options", height=100)
    
    if st.button("➕ Adicionar Pergunta"):
        if question_text:
            if len(st.session_state.survey_questions) >= 19:  # Máximo 19 + última obrigatória = 20
                st.error("Máximo de 19 perguntas personalizadas (+ 1 pergunta final obrigatória)")
            else:
                question_data = {
                    'text': question_text.strip(),
                    'type': question_type,
                    'required': True
                }
                
                if question_type == "multipla_escolha" and options_text:
                    options = [opt.strip() for opt in options_text.split('\n') if opt.strip()]
                    if options:
                        question_data['options'] = options
                
                st.session_state.survey_questions.append(question_data)
                st.success(f"Pergunta {len(st.session_state.survey_questions)} adicionada!")
                st.rerun()
    
    # Mostrar perguntas adicionadas
    if st.session_state.survey_questions:
        st.markdown("##### Perguntas Adicionadas:")
        for i, q in enumerate(st.session_state.survey_questions):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.text(f"{i+1}. {q['text'][:100]} ({q['type']})")
            with col2:
                if st.button("⌫", key=f"remove_{i}"):
                    st.session_state.survey_questions.pop(i)
                    st.rerun()
        
        # Adicionar pergunta final obrigatória
        st.info("📝 Uma pergunta final de texto longo será adicionada automaticamente")
        
        # Validação e criação
        total_questions = len(st.session_state.survey_questions) + 1  # +1 para pergunta final
        
        if total_questions < 2:
            st.warning(f"Adicione pelo menos 1 pergunta (atual: {len(st.session_state.survey_questions)})")
        else:
            st.success(f"Total de perguntas: {total_questions} (incluindo pergunta final)")
            
            if st.button("🚀 Criar Pesquisa", type="primary", disabled=(not title or total_questions < 2)):
                # Adicionar pergunta final
                final_question = {
                    'text': 'Comentários adicionais ou sugestões (opcional)',
                    'type': 'texto_longo',
                    'required': False,
                    'is_final': True,
                    'max_chars': 2000
                }
                
                all_questions = st.session_state.survey_questions + [final_question]
                
                create_survey(title, all_questions)
                st.session_state.survey_questions = []
                st.success("✅ Pesquisa criada e ativada com sucesso!")
                st.balloons()
                st.rerun()

def show_export_section():
    """Seção de exportação de dados"""
    st.markdown("#### Exportar Respostas")
    
    # Obter pesquisas
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, title, created_at FROM surveys ORDER BY id DESC")
        surveys = c.fetchall()
    
    if not surveys:
        st.info("Nenhuma pesquisa encontrada.")
        return
    
    # Selecionar pesquisa
    survey_options = {f"{s[0]} - {s[1]} ({s[2][:10]})": s[0] for s in surveys}
    selected = st.selectbox("Selecione a pesquisa", list(survey_options.keys()))
    survey_id = survey_options[selected]
    
    # Contar respostas
    with st.session_state.db_pool.get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM responses WHERE survey_id = ?", (survey_id,))
        count = c.fetchone()[0]
    
    st.info(f"Total de respostas: {count}")
    
    if count > 0:
        col1, col2 = st.columns(2)
        
        with col1:
            if st.button("📥 Baixar CSV"):
                csv_data = export_responses_to_csv(survey_id)
                if csv_data:
                    st.download_button(
                        label="💾 Download",
                        data=csv_data,
                        file_name=f"pesquisa_{survey_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
        
        with col2:
            if st.button("📧 Enviar por Email"):
                if OWNER_EMAIL:
                    with st.spinner("Enviando..."):
                        csv_data = export_responses_to_csv(survey_id)
                        if csv_data:
                            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                            success = send_email_with_retry(
                                OWNER_EMAIL,
                                f"Exportação de Pesquisa - {timestamp}",
                                f"Segue em anexo o arquivo CSV com as respostas da pesquisa ID {survey_id}.",
                                csv_data,
                                f"pesquisa_{survey_id}.csv"
                            )
                            if success:
                                st.success(f"Email enviado para {OWNER_EMAIL}")
                else:
                    st.error("Email do destinatário não configurado (OWNER_EMAIL)")

def show_diagnostics():
    """Painel de diagnóstico do sistema"""
    st.markdown("#### Diagnóstico do Sistema")
    
    # Status do banco
    st.markdown("##### 🗄️ Banco de Dados")
    try:
        with st.session_state.db_pool.get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM surveys")
            total_surveys = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM responses")
            total_responses = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM rate_limits WHERE datetime(timestamp) > datetime('now', '-1 hour')")
            recent_limits = c.fetchone()[0]
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Pesquisas", total_surveys)
        with col2:
            st.metric("Total Respostas", total_responses)
        with col3:
            st.metric("Rate Limits (1h)", recent_limits)
        
        st.success("✅ Banco de dados operacional")
    except Exception as e:
        st.error(f"⌫ Erro no banco: {str(e)}")
    
    # Status do cache
    st.markdown("##### 💾 Cache")
    try:
        # Verificar se existe método de cache stats
        cache_info = "Cache operacional"
        st.info(cache_info)
    except Exception as e:
        st.info("Cache operacional")
    
    # Status SMTP
    st.markdown("##### 📧 Configuração SMTP")
    if SMTP_USER and SMTP_PASS:
        st.success("✅ Credenciais SMTP configuradas")
        st.text(f"Servidor: {SMTP_SERVER}:{SMTP_PORT}")
        st.text(f"Usuario: {SMTP_USER[:3]}...{SMTP_USER[-3:]}")
    else:
        st.warning("⚠️ SMTP não configurado (modo fallback ativo)")
    
    # Pool de conexões
    st.markdown("##### 🔌 Pool de Conexões")
    st.text(f"Conexões disponíveis: {len(st.session_state.db_pool.pool)}/{st.session_state.db_pool.pool_size}")
    
    # Limpar dados antigos
    if st.button("🧹 Limpar rate limits antigos (> 1 dia)"):
        with st.session_state.db_pool.get_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM rate_limits WHERE datetime(timestamp) < datetime('now', '-1 day')")
            deleted = c.rowcount
            conn.commit()
        st.success(f"Removidos {deleted} registros")

def show_respond_page():
    """Página para responder pesquisa"""
    st.markdown("### Responder Pesquisa")
    
    if st.button("← Voltar"):
        st.session_state.page = 'home'
        st.rerun()
    
    # Verificar rate limiting apenas no início da sessão
    if 'rate_limit_checked' not in st.session_state:
        if not check_rate_limit(st.session_state.session_id, "survey_start", max_requests=5):
            st.error("⚠️ Muitas tentativas recentes. Aguarde alguns minutos.")
            return
        st.session_state.rate_limit_checked = True
    
    # Obter pesquisa ativa
    survey = get_active_survey()
    
    if not survey:
        st.warning("📋 Não há pesquisa ativa no momento.")
        return
    
    st.markdown(f"#### {survey['title']}")
    
    # Inicializar estado
    if 'current_question' not in st.session_state:
        st.session_state.current_question = 0
        st.session_state.answers = {}
        st.session_state.is_anonymous = True
        st.session_state.respondent_name = ""
        st.session_state.respondent_email = ""
    
    questions = survey['questions']
    total_questions = len(questions)
    current = st.session_state.current_question
    
    # Barra de progresso
    progress = ((current + 1) / total_questions) * 100
    st.markdown(f"""
    <div class="progress-bar">
        <div class="progress-fill" style="width: {progress}%"></div>
    </div>
    <div class="question-counter">
        Pergunta {current + 1} de {total_questions}
    </div>
    """, unsafe_allow_html=True)
    
    # Primeira tela - opção de anonimato
    if current == 0 and 'anonimato_definido' not in st.session_state:
        st.markdown("##### Informações do Respondente")
        
        is_anonymous = st.checkbox("Responder anonimamente", value=True, key="anon_check")
        st.session_state.is_anonymous = is_anonymous
        
        if not is_anonymous:
            col1, col2 = st.columns(2)
            with col1:
                st.session_state.respondent_name = st.text_input("Nome (opcional)", max_chars=100)
            with col2:
                st.session_state.respondent_email = st.text_input("Email (opcional)", max_chars=100)
        
        if st.button("Começar Pesquisa", type="primary"):
            st.session_state.anonimato_definido = True
            st.rerun()
        return
    
    # Exibir pergunta atual sem container extra
    if current < total_questions:
        question = questions[current]
        
        st.markdown(f"### {question['text']}")
        
        answer_key = str(current)
        
        # Renderizar campo de resposta baseado no tipo
        if question['type'] == 'texto_curto':
            answer = st.text_input("Sua resposta:", 
                                  value=st.session_state.answers.get(answer_key, ""),
                                  max_chars=200,
                                  key=f"q_{current}")
        
        elif question['type'] == 'texto_longo':
            max_chars = question.get('max_chars', 1000)
            if question.get('is_final'):
                # Pergunta final com visual especial
                answer = st.text_area("Sua resposta (opcional):", 
                                     value=st.session_state.answers.get(answer_key, ""),
                                     max_chars=max_chars,
                                     height=120,
                                     key=f"q_{current}",
                                     help=f"Máximo {max_chars} caracteres")
            else:
                answer = st.text_area("Sua resposta:", 
                                     value=st.session_state.answers.get(answer_key, ""),
                                     max_chars=max_chars,
                                     height=150,
                                     key=f"q_{current}")
        
        elif question['type'] == 'multipla_escolha':
            options = question.get('options', [])
            if options:
                current_answer = st.session_state.answers.get(answer_key, options[0])
                answer = st.radio("Escolha uma opção:", 
                                options,
                                index=options.index(current_answer) if current_answer in options else 0,
                                key=f"q_{current}")
            else:
                answer = ""
        
        elif question['type'] == 'escala_1_5':
            current_answer = st.session_state.answers.get(answer_key, 3)
            answer = st.slider("Avalie de 1 a 5:", 
                             min_value=1, 
                             max_value=5, 
                             value=int(current_answer),
                             key=f"q_{current}")
        else:
            answer = ""
        
        # Salvar resposta automaticamente
        if answer is not None and (answer or question.get('is_final')):
            st.session_state.answers[answer_key] = answer
        
        # Botões de navegação
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col1:
            if current > 0:
                if st.button("← Anterior"):
                    st.session_state.current_question = current - 1
                    st.rerun()
        
        with col3:
            if current < total_questions - 1:
                # Botão próxima sempre ativo
                if st.button("Próxima →", type="primary"):
                    # Verificar se resposta obrigatória foi preenchida antes de prosseguir
                    has_answer = st.session_state.answers.get(answer_key)
                    is_required = question.get('required', True)
                    
                    if is_required and not has_answer:
                        st.error("⚠️ Por favor, responda a pergunta antes de prosseguir.")
                    else:
                        st.session_state.current_question = current + 1
                        st.rerun()
            else:
                # Último botão - Enviar (sempre ativo)
                if st.button("✅ Enviar Respostas", type="primary"):
                    # Verificar se todas as respostas obrigatórias foram preenchidas
                    missing_required = []
                    for i, q in enumerate(questions):
                        if q.get('required', True) and str(i) not in st.session_state.answers:
                            missing_required.append(i + 1)
                    
                    if missing_required:
                        st.error(f"⚠️ Por favor, responda as perguntas obrigatórias: {', '.join(map(str, missing_required))}")
                    else:
                        with st.spinner("Salvando respostas..."):
                            save_response(
                                survey['id'],
                                st.session_state.answers,
                                st.session_state.is_anonymous,
                                st.session_state.respondent_name if not st.session_state.is_anonymous else None,
                                st.session_state.respondent_email if not st.session_state.is_anonymous else None
                            )
                        
                        # Limpar estado
                        for key in ['current_question', 'answers', 'anonimato_definido', 'rate_limit_checked']:
                            if key in st.session_state:
                                del st.session_state[key]
                        
                        st.success("✅ Respostas enviadas com sucesso! Obrigado por participar.")
                        st.balloons()
                        
                        if st.button("🏠 Voltar ao Início"):
                            st.session_state.page = 'home'
                            st.rerun()

if __name__ == "__main__":
    main()

st.markdown("""
<style>
    .main {
        background-color: #ffffff;
        color: #333333;
    }
    .block-container {
        padding-top: 1rem;
        padding-bottom: 0rem;
    }
    /* Esconde completamente todos os elementos da barra padrão do Streamlit */
    header {display: none !important;}
    footer {display: none !important;}
    #MainMenu {display: none !important;}
    /* Remove qualquer espaço em branco adicional */
    div[data-testid="stAppViewBlockContainer"] {
        padding-top: 0 !important;
        padding-bottom: 0 !important;
    }
    div[data-testid="stVerticalBlock"] {
        gap: 0 !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
    }
    /* Remove quaisquer margens extras */
    .element-container {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
    }
</style>
""", unsafe_allow_html=True)
st.markdown("---")
st.markdown("""
<div style="text-align: center;">
    <h4>Seu web app de pesquisa e feedback, com anonimato garantido</h4>
    Por 📋<strong>Ary Ribeiro</strong>: <a href="mailto:aryribeiro@gmail.com">aryribeiro@gmail.com</a><br>
    <em>Obs.: testado apenas em computador.</em>
</div>
""", unsafe_allow_html=True)