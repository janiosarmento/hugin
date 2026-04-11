# hugo-tagger — Design Spec

**Data:** 2026-04-10 (spec original) / 2026-04-11 (atualizado com implementação)
**Status:** Protótipo funcional
**Repositório:** Projeto independente (não acoplado a nenhum blog específico)

## Visão geral

CLI + TUI em Python que analisa posts de blogs Hugo, submete o conteúdo a um LLM via API OpenAI-compatible e permite ao usuário revisar e aplicar tags e sumários gerados — com revisão humana no loop.

Funciona com qualquer blog Hugo, em qualquer idioma. Cada diretório de posts é tratado como um universo independente com seu próprio pool de tags.

## Princípios

- **Genérico:** não assume estrutura de nenhum blog específico. Recebe o diretório de posts como argumento.
- **Idioma do universo:** tags e sumários são gerados no idioma do conteúdo. Posts em PT geram tags em PT; posts em EN geram tags em EN.
- **Humano no loop:** o usuário revisa e pode vetar qualquer tag ou sumário antes da injeção via TUI.
- **LLM-agnóstico:** fala com qualquer endpoint `/v1/chat/completions`. Motores de AI cadastrados em arquivo de configuração, selecionáveis na TUI com persistência da escolha.

## Interface CLI

```
hugo-tagger [diretório] [opções]
```

### Argumento posicional

| Argumento | Comportamento |
|---|---|
| `[diretório]` | Diretório contendo os posts Hugo. Default: diretório atual (`$PWD`). |

### Flags

| Flag | Descrição | Default |
|---|---|---|
| `--batch N` | Máximo de posts a carregar (0 = todos) | 0 |
| `--report` | Exibe estatísticas dos posts, sem chamar LLM | off |
| `--engine ID` | Identificador do motor de AI (override da última seleção) | último usado |
| `--model MODEL` | Override do modelo (ignora o modelo padrão do motor) | modelo do motor |

## Ordenação de posts

Todos os posts do diretório são carregados e ordenados por data de publicação (mais recentes primeiro). O `--batch N` limita a quantidade; `--batch 0` (default) carrega todos.

## Fluxo de execução

### Modo padrão (`hugo-tagger [diretório]`)

```
1. Ler todos os .md do diretório (não-recursivo)
2. Parsear frontmatter YAML de cada um (posts com TOML frontmatter são ignorados com aviso)
3. Ordenar por data de publicação (mais recentes primeiro)
4. Aplicar --batch N (0 = todos)
5. Coletar pool de tags existentes no universo (de todos os posts)
   O pool é atualizado dinamicamente conforme tags são aplicadas durante a sessão
6. Abrir TUI com a lista de posts em DataTable
7. Usuário navega com setas, escolhe ação:
   - 't' Tags: consulta LLM para sugestão de tags
   - 's' Summary: consulta LLM para sugestão de sumário
   - 'e' Engine: abre modal de seleção de engine/modelo
8. LLM é chamado on-demand (não "a frio") quando o usuário dispara a ação
9. Resultado exibido para revisão com Apply/Skip
10. Apply escreve no frontmatter (YAML, sort_keys=False para preservar ordem)
11. Se o post não tinha lastmod, adiciona com timestamp do processamento
12. State file atualizado
```

### Modo relatório (`--report`)

Só lê frontmatter, sem chamar LLM:

```
Posts sem tags:              3
Posts editados após tags:    12
Posts atualizados:           385
Total:                       400

Tags únicas:                 47

Tags possivelmente duplicadas:
  selfhosted ↔ self-hosted   (similaridade: 90%)
  docker ↔ docker-compose    (prefixo comum)
```

A detecção de duplicatas usa `SequenceMatcher` (ratio >= 0.8) e análise de prefixos comuns como diagnóstico. Nenhuma ação automática.

## TUI (Textual)

### Tela principal de revisão

```
┌───────────────────────────────────────────────────────────────────┐
│  hugo-tagger                                                       │
├───────────────────────────────────────────────────────────────────┤
│  Posts (DataTable 60%)      │  Engine: cerebras (llama-4)          │
│                             │  3/40 — systemd-timers.md            │
│  ✓ como-criei-blog.md       │                                      │
│  ⠹ systemd-timers.md        │  title       Timers do Systemd       │
│    immich-setup.md           │  date        2026-03-15              │
│    ...                       │  lastmod     2026-03-20              │
│                             │  draft       false                    │
│                             │  description Como usar systemd...     │
│                             │  tags        systemd, linux, cron     │
│                             │                                      │
│                             │  Existing:                           │
│                             │    [x] systemd                       │
│                             │    [x] linux                         │
│                             │    [ ] cron  (vetada)                │
│                             │  Suggested:                          │
│                             │    [x] agendamento                   │
│                             │    [x] automação                     │
│                             │  [Manual tags ___________________]   │
│                             │  [Apply] [Skip]                      │
├───────────────────────────────────────────────────────────────────┤
│  q Quit  t Tags  s Summary  e Engine  escape Back                  │
└───────────────────────────────────────────────────────────────────┘
```

### Painel esquerdo (60%)

- **DataTable** com zebra stripes e cursor de linha
- Coluna de status (1 char): spinner animado durante consulta, ✓ após aplicação
- Coluna de título do post
- Navegação com setas

### Painel direito (40%)

- **Engine label** no topo (engine + modelo atual)
- **Progress** (N/total — filename)
- **Metadados** do frontmatter: Rich Table com zebra stripes (background #808080), campo em bold+accent, valor normal. description sempre penúltimo, tags sempre último.
- **Área de revisão** (aparece após LLM responder):
  - Tags: checkboxes para existing + suggested + input de tags manuais (comma-separated)
  - Summary: current vs suggested com contagem de caracteres
- **Botões Apply/Skip**: ocultos até o LLM responder, focus automático no Apply

### Keybindings

| Tecla | Ação | Estado |
|---|---|---|
| `t` | Consultar LLM para tags | browsing |
| `s` | Consultar LLM para sumário | browsing |
| `e` | Abrir modal de seleção de engine/modelo | browsing |
| `Escape` | Voltar de reviewing para browsing, ou fechar modal | reviewing |
| `q` | Sair do app | qualquer |
| `↑/↓` | Navegar posts | browsing |

### Estados da TUI

```
BROWSING → (t/s) → LOADING → (resposta LLM) → REVIEWING → (Apply/Skip/Escape) → BROWSING
```

### Modal de seleção de engine

Dois níveis:

1. **Lista de engines**: mostra id, modelo, status (ready/no key), ● no engine ativo
2. **Lista de modelos**: ao selecionar um engine, consulta `/v1/models` do endpoint e lista modelos disponíveis (filtra embeddings)

Escape no segundo nível volta para o primeiro. A seleção persiste em `~/.hugo-tagger/last_engine.json`.

### Revisão de tags

- **Existing tags** com checkboxes pré-marcados — desmarcar remove a tag do post
- **Suggested tags** com checkboxes pré-marcados — desmarcar rejeita a sugestão
- **Manual tags** input (comma-separated) — normalizadas e adicionadas à lista final
- **Apply** escreve a lista final completa (kept + suggested + manual), substituindo tags anteriores
- Notificação: `filename.md: +3, +1 manual, -1`

### Revisão de sumário

- **Current description** exibida como texto (se existir)
- **Suggested description** exibida com contagem de caracteres
- **Apply** sobrescreve o campo `description` do frontmatter

## Prompts para o LLM

### Prompt de tags

```
You are a blog post tag generator. Analyze the following blog post and suggest relevant tags.

RULES:
- Tags must be lowercase
- Multi-word tags use hyphens as separator (e.g., "self-hosted")
- Maximum 3 words per tag
- No articles (a, the, o, um, os, as, les, etc.)
- Keep tags as short as possible
- Suggest between 3 and 7 tags per post
- Tags must be in the same language as the post content
- PRESERVE accents and diacritics (e.g., "automação" not "automacao", "réseau" not "reseau")
- STRONGLY prefer reusing tags from the existing pool below
- Only create new tags when no existing tag adequately covers the topic

EXISTING TAGS IN THIS BLOG (most used first):
{pool}

POST CONTENT:
{content}

Respond with a JSON array of strings, nothing else. Example: ["tag-one", "tag-two", "tag-three"]
```

### Prompt de sumário

```
You are a blog post summary writer. Write a concise meta description for the following blog post.

RULES:
- The summary must be between 140 and 160 characters long
- It must be in the same language as the post content
- It should accurately describe what the reader will learn or find
- It should be compelling and invite the reader to click
- Do not use clickbait
- Do not start with "This post" or "In this article" or equivalent
- Respond with the summary text only, nothing else — no quotes, no explanation

CURRENT DESCRIPTION (improve or replace):
{current_description}

POST CONTENT:
{content}
```

### Parse da resposta do LLM

**Tags:** strip de code fences → strip de texto fora do `[...]` → `json.loads()` → fallback regex → erro na TUI com opção de retry.

**Sumário:** strip de code fences → strip de aspas ao redor → texto limpo.

### Truncamento de posts longos

Se `len(texto) / 4 > 4000` (estimativa de tokens), envia apenas: título, descrição, headings e primeiros ~1000 caracteres do conteúdo.

### Pool de tags com frequência

Enviado ao LLM ordenado por frequência (decrescente), com contagem, limitado a 100 tags:

```
selfhosted (12), linux (10), docker (8), hugo (7), cloudflare (5), ...
```

## Normalização de tags (safety net)

Após receber tags do LLM, o código aplica:

1. Lowercase
2. Substituir espaços por hífens
3. Remover artigos soltos (lista por idioma: PT, EN, ES, FR)
4. Truncar tags com mais de 3 palavras
5. Deduplicar contra tags existentes do post
6. Deduplicar contra o pool (merge case-insensitive, preferindo a forma do pool)

Acentos e caracteres Unicode são **preservados**. A responsabilidade de gerar URLs limpas é do Hugo (`removePathAccents` no config).

## Configuração

Diretório: `~/.hugo-tagger/`

| Arquivo | Conteúdo |
|---|---|
| `engines.toml` | Cadastro de motores de AI |
| `last_engine.json` | Última engine+modelo selecionados (persistência) |
| `state/<hash>.json` | State file por diretório de posts |

### engines.toml

```toml
[cerebras]
url = "https://api.cerebras.ai/v1"
model = "llama-4-scout-17b-16e-instruct"
timeout = 30

[openai]
url = "https://api.openai.com/v1"
model = "gpt-4o"
timeout = 30

[lmstudio]
url = "http://192.168.3.36:1234/v1"
model = "google/gemma-4-26b-a4b"
timeout = 300
```

Se o arquivo não existir, é criado com engines padrão.

### Chave de API

Variável de ambiente `{IDENTIFICADOR}_API_KEY` (seção em uppercase). Motores em redes privadas (localhost, 192.168.x.x, 10.x.x.x, 172.16-31.x.x) não exigem API key.

### State file

`~/.hugo-tagger/state/<sha256-hash-16chars>.json`:

```json
{
  "directory": "/home/user/blog/content/posts",
  "posts": {
    "systemd-timers.md": {
      "last_processed": "2026-04-10T14:30:00"
    }
  }
}
```

## Campos do frontmatter utilizados

| Campo | Uso |
|---|---|
| `tags` | Lista de tags. O app lê e escreve (substituição completa). |
| `description` | Sumário do post. O app lê e escreve via função Summary. |
| `lastmod` | Data da última edição. Se ausente, o app adiciona ao processar. |
| `date` | Data de publicação. Usado para ordenação. |
| `title` | Exibido na DataTable e no painel de metadados. Incluído no prompt truncado. |
| `draft` | Posts draft são processados normalmente (decisão do usuário). |

Todos os campos do frontmatter são exibidos na Rich Table de metadados. Ao salvar, o writer reordena o frontmatter garantindo `description` como penúltimo campo e `tags` como último. Os demais campos preservam a ordem original.

## Dependências Python

| Pacote | Uso |
|---|---|
| `python-frontmatter` | Parse e escrita de frontmatter YAML |
| `httpx` | Chamadas HTTP async ao endpoint LLM |
| `click` | CLI (parse de argumentos e flags) |
| `textual` | TUI interativa |
| `pytest` | Testes (dev) |
| `pytest-httpx` | Mock de chamadas httpx (dev) |
| `pytest-asyncio` | Testes async (dev) |

## Estrutura do projeto

```
hugo-tagger/
├── pyproject.toml
├── PROJECT.md              # Este arquivo (design spec)
├── GUIDELINES.md           # Diretrizes de desenvolvimento
├── PROGRESS.md             # Log de progresso da implementação
├── src/
│   └── hugo_tagger/
│       ├── __init__.py
│       ├── cli.py            # Entrypoint CLI (click)
│       ├── engines.py        # Cadastro de motores + persistência de seleção
│       ├── scanner.py        # Leitura de posts, ordenação, pool de tags, detecção de duplicatas
│       ├── llm.py            # Prompts, chamadas LLM, parse de respostas (tags + sumário)
│       ├── normalizer.py     # Normalização de tags (safety net)
│       ├── state.py          # Leitura/escrita do state file
│       ├── writer.py         # Escrita de tags e sumários no frontmatter
│       └── tui/
│           ├── __init__.py
│           ├── app.py            # App Textual principal
│           ├── engine_picker.py  # Modal de seleção de engine/modelo
│           ├── selection.py      # Tela de seleção de posts (legado, não usada)
│           └── review.py         # Tela principal de revisão (tags + sumário)
└── tests/
    ├── conftest.py
    ├── fixtures/              # Posts .md de exemplo
    │   ├── post-with-tags.md
    │   ├── post-no-tags.md
    │   ├── post-draft.md
    │   └── post-toml-frontmatter.md
    ├── test_normalizer.py
    ├── test_scanner.py
    ├── test_llm.py
    ├── test_state.py
    └── test_engines.py
```

## Testes

Framework: `pytest`. 54 testes cobrindo módulos de lógica pura.

| Módulo | O que testa |
|---|---|
| `normalizer` | Lowercase, hífens, remoção de artigos, truncamento, dedup, preservação de acentos |
| `scanner` | Carga de posts, detecção de TOML, parse de tags, priorização, pool, duplicatas |
| `llm` | Parse de JSON limpo, code fences, texto extra, fallback regex, truncamento, prompt |
| `state` | Load/save, mark_processed, get_last_processed, hash de diretório |
| `engines` | is_local (localhost, 127.0.0.1, redes privadas), available, API key lookup |

TUI sem testes automatizados.

## Fora de escopo (futuro)

- **Modo automático (`--auto`):** aplicar tags/sumários sem TUI. Útil para automação em CI/cron.
- **Arquivo `tagger.yaml` de regras:** configuração de regras por projeto em vez de hardcoded.
- **Execução periódica:** integração com cron/launchd.
- **Leitura recursiva:** processar subdiretórios de posts.
- **Page bundles:** suporte a posts como `meu-post/index.md`.
- **TOML frontmatter:** leitura e escrita de frontmatter TOML (`+++`).
