# Catálogo de Livros — Contexto do Projeto

## O que é

Aplicativo web mobile-first para digitalizar e organizar uma biblioteca pessoal.
O usuário fotografa a capa e páginas internas de um livro pelo celular; uma IA analisa as imagens e extrai automaticamente os metadados (título, autor, editora, ano, ISBN, idioma, capítulos). Os livros ficam salvos localmente com busca full-text.

---

## Stack técnica

| Camada | Tecnologia |
|---|---|
| Backend | Python 3 + Flask |
| Banco de dados | SQLite com FTS5 (full-text search) |
| IA de visão | Google Gemini (`gemini-flash-latest` via API v1beta) |
| Frontend | HTML/CSS/JS vanilla — SPA com seções show/hide |
| Armazenamento de imagens | Disco local (`uploads/`, nome UUID) |

Sem frameworks externos no frontend (sem React, jQuery, Bootstrap). CSS mobile-first puro.

---

## Estrutura de arquivos

```
catalog/
├── app.py                  ← Backend Flask (API REST + inicialização do banco)
├── templates/
│   └── index.html          ← SPA completa (HTML + CSS + JS inline)
├── uploads/                ← Imagens salvas (nomeadas com UUID)
├── catalog.db              ← Banco SQLite (criado automaticamente)
├── CONTEXTO.md             ← Este arquivo
└── README.md               ← Instruções de instalação
```

---

## Banco de dados

### Tabela `books`
| Campo | Tipo | Descrição |
|---|---|---|
| id | INTEGER PK | Auto-incremento |
| title | TEXT | Título do livro |
| author | TEXT | Autor |
| publisher | TEXT | Editora |
| year | TEXT | Ano de publicação |
| isbn | TEXT | ISBN |
| language | TEXT | Idioma |
| created_at | TIMESTAMP | Data de cadastro |

### Tabela `images`
| Campo | Tipo | Descrição |
|---|---|---|
| id | INTEGER PK | Auto-incremento |
| book_id | INTEGER FK | Referência a `books.id` (CASCADE DELETE) |
| filename | TEXT | Nome do arquivo em `uploads/` |
| created_at | TIMESTAMP | Data de upload |

### FTS5
Tabela virtual `books_fts` espelha `books` via triggers (INSERT/UPDATE/DELETE).
Permite busca full-text em `title`, `author`, `publisher`, `isbn`.

---

## API REST

| Método | Endpoint | Descrição |
|---|---|---|
| `GET` | `/` | Serve o SPA (index.html) |
| `POST` | `/scan` | Recebe `multipart/form-data` com campo `image`, retorna JSON com dados extraídos pela IA |
| `POST` | `/books` | Cria livro. Body JSON: campos do livro + `images: [{b64, type}]` |
| `GET` | `/books` | Lista todos os livros com suas imagens |
| `GET` | `/search?q=...` | Busca full-text via FTS5 |
| `POST` | `/books/<id>/images` | Adiciona imagem avulsa a um livro existente |
| `GET` | `/uploads/<filename>` | Serve arquivo de imagem salvo |

---

## IA — Google Gemini

- **Modelo:** `gemini-flash-latest` (gratuito via Google AI Studio)
- **SDK:** `google-genai` (versão nova; o pacote `google-generativeai` está deprecated)
- **API version:** `v1beta` (obrigatório para o tier gratuito funcionar)
- **Chave:** variável de ambiente `GEMINI_API_KEY`, salva em `~/.zshrc`

### Prompt enviado para cada imagem:
```
Extract from this book image: title, author, publisher, year, isbn, language.
If this is a table of contents, extract all chapter/section titles and page numbers
as a 'chapters' array with 'title' and 'page' keys.
Return JSON only, no explanation.
```

A resposta é JSON puro. Se vier envolta em bloco markdown (` ```json `), o código strip o bloco antes de parsear.

---

## Fluxo de uso — múltiplas fotos

1. Usuário fotografa a **capa** → IA extrai todos os metadados → formulário pré-preenchido
2. Usuário toca `+` na faixa de miniaturas → fotografa **página interna** (índice, verso da capa, etc.)
3. IA analisa cada página adicional:
   - Campos vazios são preenchidos com dados novos
   - Capítulos são **acumulados** (não substituídos)
4. Usuário revisa/corrige o formulário
5. Salva → todas as fotos são gravadas em `uploads/` e associadas ao livro no banco

---

## Como rodar

```bash
# Instalar dependências
pip3 install flask google-genai pillow

# Configurar chave da IA (Google AI Studio — gratuito)
export GEMINI_API_KEY=sua-chave-aqui

# Iniciar servidor
python3 app.py
```

**Acesso local:** http://localhost:8080

**Acesso pelo celular** (mesma rede Wi-Fi):
```bash
ipconfig getifaddr en0   # descobre o IP local no macOS
# Abrir no celular: http://<IP>:8080
```

---

## Problemas encontrados e soluções

| Problema | Causa | Solução |
|---|---|---|
| Porta 5000 indisponível | macOS reserva a 5000 para AirPlay Receiver | Mudado para porta 8080 |
| Erro de autenticação Anthropic | `ANTHROPIC_API_KEY` não definida | Exportada no `~/.zshrc` |
| Créditos Anthropic zerados | Conta sem saldo | Migrado para Google Gemini (gratuito) |
| `google-generativeai` deprecated | SDK antigo descontinuado | Migrado para `google-genai` |
| Cota Gemini = 0 (`limit: 0`) | Chaves criadas via Google Cloud Console não têm free tier ativo | Nova chave gerada no Google AI Studio (`aistudio.google.com`) |
| Modelo `gemini-1.5-flash` não encontrado | SDK novo usa endpoint diferente | Usando `gemini-flash-latest` com `api_version: v1beta` |

---

## Dependências Python

```
flask
google-genai
pillow
```

Instalação: `pip3 install flask google-genai pillow --break-system-packages`
(flag necessária no macOS com Python do Homebrew)
