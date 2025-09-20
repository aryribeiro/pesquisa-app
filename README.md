Obs.: caso o app esteja no modo "sleeping" (dormindo) ao entrar, basta clicar no botão que estará disponível e aguardar, para ativar o mesmo. 
<img width="797" height="660" alt="Screenshot 2025-09-20 at 11-45-46 Pesquisa App! - por Ary Ribeiro · Streamlit" src="https://github.com/user-attachments/assets/03df525c-e621-48b3-a7f7-3f480862ed12" />

# Pesquisa App!

Web app de pesquisa e feedback, com anonimato garantido. Desenvolvido em Python e Streamlit, pronto para deploy no Streamlit Cloud.

## 🚀 Funcionalidades

### Para Administradores
- Autenticação segura com bcrypt
- Criar pesquisas com 2-20 perguntas (múltiplos tipos)
- Dashboard com estatísticas em tempo real
- Exportação de respostas em CSV
- Envio automático por email
- Painel de diagnóstico do sistema

### Para Respondentes
- Interface estilo Typeform (uma pergunta por vez)
- Opção de responder anonimamente
- Barra de progresso visual
- Navegação intuitiva entre perguntas
- Validação em tempo real

## 📋 Pré-requisitos

- Python 3.8+
- Conta no Streamlit Cloud (para deploy)
- Conta Gmail com App Password (opcional, para envio de emails)

## 🔧 Instalação Local

1. Clone o repositório:
```bash
git clone https://github.com/aryribeiro/pesquisa-app.git
cd pesquisa-anonima
```

2. Crie ambiente virtual:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows
```

3. Instale dependências:
```bash
pip install -r requirements.txt
```

4. Configure variáveis de ambiente:
```bash
cp .env.example .env
# Edite .env com suas configurações
```

5. Execute localmente:
```bash
streamlit run app.py
```

## 🌐 Deploy no Streamlit Cloud

### Passo 1: Preparar Repositório

1. Crie repositório no GitHub
2. Faça upload dos arquivos:
   - `app.py`
   - `requirements.txt`
   - `README.md`
   - `.env.example`

### Passo 2: Configurar no Streamlit Cloud

1. Acesse [share.streamlit.io](https://streamlit.app)
2. Clique em "New app"
3. Conecte seu GitHub
4. Selecione:
   - Repository: `seu-usuario/pesquisa-app`
   - Branch: `main`
   - Main file path: `app.py`

### Passo 3: Configurar Secrets

No painel do Streamlit Cloud, vá em Settings → Secrets e adicione:

```toml
SMTP_USER = "seu-email@gmail.com"
SMTP_PASS = "sua-app-password"
OWNER_EMAIL = "destinatario@example.com"
SECRET_KEY = "uma-chave-secreta-aleatoria"
```

### Passo 4: Deploy

Clique em "Deploy" e aguarde. O app estará disponível em:
`https://seu-app.streamlit.app`

## 🔐 Configuração de Email (Gmail)

### Criar App Password:

1. Acesse [myaccount.google.com](https://myaccount.google.com)
2. Segurança → Verificação em duas etapas (ative se necessário)
3. Senhas de app → Selecionar app → Outro
4. Nome: "Pesquisa App"
5. Copie a senha gerada (16 caracteres)

### Configurar no .env:
```env
SMTP_USER=seu-email@gmail.com
SMTP_PASS=xxxx-xxxx-xxxx-xxxx  # App password
OWNER_EMAIL=destinatario@example.com
```

## 🧪 Testes

### Modo Sandbox (sem SMTP):

Para testar sem configurar email, deixe `SMTP_USER` vazio:
```env
SMTP_USER=
SMTP_PASS=
```
O sistema salvará CSVs localmente como fallback.

### Teste Manual:

1. **Admin**:
   - Senha padrão: `admin123`
   - Criar pesquisa com 5 perguntas
   - Verificar dashboard
   - Exportar respostas

2. **Respondente**:
   - Responder anonimamente
   - Testar todos os tipos de pergunta
   - Verificar validações

## 📊 Arquitetura

### Banco de Dados (SQLite)
- `admin_config`: Configurações e senha admin
- `surveys`: Pesquisas criadas
- `responses`: Respostas dos usuários
- `rate_limits`: Controle de rate limiting

### Otimizações para Performance
- Pool de conexões SQLite (5 conexões)
- Cache com `st.cache_data` (TTL 60s)
- WAL mode + PRAGMA optimizations
- ThreadPoolExecutor para tasks assíncronas
- Rate limiting por sessão

### Segurança
- Passwords hasheados com bcrypt
- Rate limiting (5 req/min por sessão)
- Validação e sanitização de inputs
- Sessões únicas por usuário
- Proteção contra SQL injection

## 🎯 Capacidade

O sistema foi otimizado para suportar:
- **400-500 usuários simultâneos**
- **1000+ respostas por pesquisa**
- **Exportações de até 10MB**

## 🐛 Troubleshooting

### Erro de SMTP
- Verifique App Password do Gmail
- Confirme 2FA ativado
- Teste com porta 587 (TLS)

### Banco travado
- Delete `survey.db` para reset completo
- Verifique permissões de escrita

### Performance lenta
- Limpe rate_limits antigos (Diagnóstico)
- Verifique cache (botão clear cache)
- Reduza pool de conexões se necessário

## 📝 Notas de Desenvolvimento

### Estrutura de Perguntas
```python
{
    'text': 'Pergunta',
    'type': 'texto_curto|texto_longo|multipla_escolha|escala_1_5',
    'required': True/False,
    'options': ['op1', 'op2'],  # apenas para múltipla escolha
    'max_chars': 2000  # para texto longo
}
```

### Fluxo de Dados
1. Admin cria pesquisa → SQLite
2. Cache atualizado (60s TTL)
3. Usuário responde → Validação → SQLite
4. Export → CSV → Email/Local

## 🤝 Suporte

Para problemas ou dúvidas:
1. Verifique logs no Streamlit Cloud
2. Console de diagnóstico no admin

## 📄 por Ary Ribeiro

aryribeiro@gmail.com

---


**Desenvolvido com ❤️ usando Python e Streamlit**
