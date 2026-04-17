# Hugin + Munin Merge — Design Spec

**Data:** 2026-04-17
**Status:** Aprovado
**Motivacao:** Atrito no workflow — sair de um programa, entrar no outro, relocalizar o post. Unificar tudo numa TUI so elimina esse custo.

## Decisoes de design

- Comando unico: `hugin`. Entry point `munin` removido.
- Tela unica unificada (Abordagem A): funde `ReviewScreen` + `MuninScreen` numa unica `HuginScreen`.
- Painel direito contextual — muda de conteudo conforme a acao, sem tabs ou screen stack.
- Editor interno substitui Vim: campos de frontmatter editaveis + TextArea pro corpo Markdown.
- Edicao de titulo edita so o campo `title` no frontmatter (nao renomeia o arquivo).
- Strip all links removido (substituido pela remocao seletiva via `l`).
- Suggest topics mapeado pra `u`.
- Config de links renomeado de `~/.hugin/munin.toml` pra `~/.hugin/links.toml` com fallback pro nome antigo + warning.

## 1. Arquitetura e estrutura de arquivos

O pacote `munin/` e absorvido. Logica pura move pra `hugin/`. TUI do Munin descartada — codigo fundido na screen unificada.

```
src/
  hugin/
    cli.py              # Entry point unico (absorve init do Munin)
    scanner.py           # (sem mudanca)
    writer.py            # (sem mudanca)
    llm.py               # Absorve prompts do Munin (anchor, suggest, retry)
    engines.py           # (sem mudanca)
    normalizer.py        # (sem mudanca)
    state.py             # (sem mudanca)
    linker.py            # <- de munin/linker.py
    embeddings.py        # <- de munin/embeddings.py
    hugo.py              # <- de munin/hugo.py
    config.py            # <- de munin/config.py (MuninConfig -> LinksConfig)
    tui/
      app.py             # HuginApp (absorve init do MuninApp: site, index, config)
      review.py          # HuginScreen unificada
      engine_picker.py   # (sem mudanca)
      editor.py          # NOVO: EditorScreen
      tag_manager.py     # (sem mudanca)
```

`src/munin/` e deletado. Entry point `munin` removido do `pyproject.toml`.

## 2. CLI unificado

Fluxo de inicializacao:

```
1. Ler posts do diretorio
2. Resolver URLs Hugo (HugoSite) — warnings impressos no terminal
3. Construir/atualizar indice de embeddings (com progress)
4. Carregar config de links (~/.hugin/links.toml, fallback ~/.hugin/munin.toml + warning)
5. Carregar state, pool de tags, engine
6. Lancar TUI com tudo disponivel
```

Flags: `--batch`, `--report`, `--engine`, `--model` (sem mudancas).

`--report` mostra stats unificadas (tags + embeddings + links).

Loop de restart (exit code 42 pro clear caches) mantido.

### Config de links — fallback

```python
def load_config():
    new_path = CONFIG_DIR / "links.toml"
    old_path = CONFIG_DIR / "munin.toml"
    if new_path.exists():
        return _parse(new_path)
    if old_path.exists():
        warn("~/.hugin/munin.toml is deprecated, rename to links.toml")
        return _parse(old_path)
    return default_config()
```

## 3. TUI unificada — HuginScreen

Layout: post list a esquerda (3fr), painel contextual a direita (2fr).

### Keybindings

| Tecla     | Acao                       | Origem |
|-----------|----------------------------|--------|
| `t`       | Tags (LLM)                | Hugin  |
| `s`       | Summary (LLM)             | Hugin  |
| `i`       | Incoming links             | Munin  |
| `o`       | Outgoing links (LLM)      | Munin  |
| `l`       | List links (remocao seletiva) | Munin  |
| `u`       | Suggest topics (LLM)      | Munin  |
| `e`       | Editor                    | Novo   |
| `n`       | Engine picker              | Ambos  |
| `m`       | Manage tags                | Hugin  |
| `c`       | Clear caches & restart     | Munin  |
| `q`       | Quit                       | Ambos  |
| `Escape`  | Voltar ao browsing         | Ambos  |

### Painel direito — estados

- **Browsing**: metadados do post (Rich Table com frontmatter, links in/out count)
- **Tags**: checkboxes existing + suggested + manual input + Apply/Skip
- **Summary**: current vs suggested com TextArea editavel + Apply/Skip
- **Incoming**: lista clicavel de posts similares (ClickableLink)
- **Outgoing**: checkboxes com contexto em torno do anchor + Apply/Skip
- **List**: checkboxes de links existentes pra remocao seletiva + Remove/Skip
- **Suggest**: lista de ideias + copy to clipboard
- **Editor**: push_screen(EditorScreen) — tela cheia separada

### Organizacao interna

Metodos agrupados por dominio com prefixo:

- Tags: `action_tags()`, `_call_llm_tags()`, `_display_tags()`, `_apply_tags()`
- Summary: `action_summary()`, `_call_llm_summary()`, `_display_summary()`, `_apply_summary()`
- Incoming: `action_incoming()`, `_show_incoming()`
- Outgoing: `action_outgoing()`, `_run_outgoing()`, `_show_outgoing()`, `_do_apply_outgoing()`
- List: `action_list_links()`, `_show_existing_links()`, `_do_remove_selected()`
- Suggest: `action_suggest()`, `_run_suggest()`, `_show_suggestions()`
- Editor: `action_editor()` — push EditorScreen
- Comum: spinner, navigation, engine picker, metadata panel

## 4. EditorScreen

Tela separada via push_screen. Tela cheia.

### Layout

**Bloco superior — Frontmatter:**

Campos editaveis como `Input` widgets, um por campo do frontmatter. `title` editavel (correcao de typos). `tags` read-only (tem `t` dedicado). Labels com nome do campo.

**Bloco inferior — Corpo:**

`TextArea` com conteudo Markdown. Syntax highlighting de Markdown (suporte nativo do Textual TextArea).

### Fluxo de save

1. `Ctrl+S` ou botao Save:
   - Le valores dos inputs de frontmatter
   - Le conteudo do TextArea
   - Reconstroi frontmatter YAML
   - Escreve em arquivo temporario
   - Renomeia atomico pro arquivo original (mesmo padrao de `write_post_with_links`)
   - Atualiza objeto `Post` em memoria (metadata, content, tags, has_tags)
   - Re-computa embedding do post se corpo mudou
   - Notifica "saved"
2. `Escape`:
   - Se dirty: confirma descartar mudancas (modal)
   - Se limpo: volta direto

### Keybindings

| Tecla     | Acao                          |
|-----------|-------------------------------|
| `Ctrl+S`  | Salvar                        |
| `Escape`  | Voltar (confirma se dirty)    |
| `Tab`     | Navegar entre campos          |

## 5. Eliminacao de codigo duplicado

Codigo identico nas duas TUIs que se consolida:

| Componente | Hugin | Munin | Unificado |
|-----------|-------|-------|-----------|
| Spinner (SPINNER_FRAMES, tick, start, stop) | ReviewScreen | MuninScreen | HuginScreen |
| Engine picker action | ReviewScreen | MuninScreen | HuginScreen |
| Metadata panel | `_update_right_panel()` | `_update_detail_panel()` | `_update_detail_panel()` (inclui link counts) |
| Navigation | `on_data_table_row_highlighted()` | idem | uma implementacao |
| State machine | STATE_BROWSING/LOADING/REVIEWING | idem | uma definicao |
| Post table mount | `on_mount()` populando DataTable | idem | uma implementacao |

Codigo especifico do Munin sem par no Hugin (incoming, outgoing, suggest, list links) se adiciona como metodos novos.

## 6. Prompts LLM

Os prompts do Munin (ANCHOR_SYSTEM_PROMPT, ANCHOR_USER_TEMPLATE, SUGGEST_PROMPT, RETRY_PROMPT) movem de `munin/tui.py` pra `hugin/llm.py`, junto com os prompts de tags e summary que ja estao la.

A funcao `_parse_anchor_response()` e `_parse_suggestions()` movem pra `hugin/llm.py` como funcoes de modulo (nao metodos de screen).

## 7. Testes

- `tests/munin/test_linker.py` → `tests/test_linker.py` (imports atualizados para `hugin.linker`)
- `tests/munin/test_hugo.py` → `tests/test_hugo.py` (imports atualizados para `hugin.hugo`)
- Parse de anchor response e suggestions → novos testes em `tests/test_llm.py`
- Testes existentes do Hugin: so atualizar imports se necessario
- Sem testes de TUI (mantendo politica atual)
- `tests/munin/` deletado

## 8. Widgets auxiliares do Munin

Widgets definidos em `munin/tui.py` que sao reutilizados:

- `ClickableLink` — move pra `hugin/tui/review.py` (usado pra incoming links)
- `ConfirmClearScreen` — move pra `hugin/tui/review.py` (usado pelo `c` clear caches)
- `ConfirmStripLinksScreen` — descartado (strip all links removido)

## 9. Itens removidos

- Entry point `munin` do pyproject.toml
- Pacote `src/munin/` inteiro
- `Delete/Backspace` strip all links (substituido por remocao seletiva via `l`)
- Keybinding `v` para Vim (substituido pelo editor interno `e`)
- `munin/state.py` SessionState — o estado de sessao do Munin (cached incoming/outgoing) se torna estado local da HuginScreen
- Banner ASCII do Munin
