# Catálogo de Livros

Aplicativo mobile para digitalizar e organizar livros. Detecta o ISBN no código de barras, busca os metadados grátis na Google Books API e só usa IA (Gemini) como último recurso.

## Instalação

```bash
brew install zbar
pip3 install flask google-genai pillow pyzbar --break-system-packages
export GEMINI_API_KEY=sua-chave-aqui
python3 app.py
```

**Nota**: A chave da API só é usada quando o livro não tem ISBN legível. Gere em [Google AI Studio](https://aistudio.google.com) (gratuito).

## Pipeline de extração

1. **Código de barras (pyzbar)**: detecta ISBN-13/ISBN-10 nas fotos. Rápido, leve, sem IA.
2. **Google Books API**: busca título/autor/editora/ano/idioma pelo ISBN. Grátis, sem chave.
3. **Gemini (último recurso)**: só é chamado se nenhuma foto tiver ISBN legível.

## Acesso

**Local**: http://localhost:8080

**Via celular** (mesma rede Wi-Fi):
```bash
# Descubra seu IP local:
ipconfig getifaddr en0   # macOS
# Acesse no celular: http://<IP>:8080
```

## Funcionalidades

- **Digitalizar**: fotografe contracapa (código de barras) ou capa — ISBN é resolvido na Google Books API; sem ISBN, cai no Gemini.
- **Múltiplas fotos**: junte capa + índice + verso. Campos vazios são preenchidos pelo melhor sinal disponível.
- **Sumário persistido**: capítulos extraídos do índice ficam salvos junto ao livro.
- **Fichamento sob demanda**: na tela de detalhe, gere fichamento por capítulo via Gemini —
  - **Pelo título**: usa o conhecimento da IA sobre a obra (rápido).
  - **Com fotos**: fotografe páginas do capítulo e a IA fichamenta o conteúdo real.
  - Pode regenerar a qualquer momento.
- **Biblioteca**: lista todos os livros com capa; clique para ver detalhe.
- **Busca**: full-text search via SQLite FTS5.
