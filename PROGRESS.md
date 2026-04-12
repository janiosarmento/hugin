# hugo-tagger — Progresso de Implementação

## Fases

| # | Fase | Status |
|---|---|---|
| 1 | Scaffolding (pyproject.toml, estrutura de pacotes) | concluído |
| 2 | Módulos core (engines, state, normalizer, scanner, writer, llm) | concluído |
| 3 | CLI (click) | concluído |
| 4 | TUI (Textual: app, review, engine_picker) | concluído |
| 5 | Testes (pytest + fixtures) | concluído |
| 6 | Iterações pós-protótipo | concluído |
| 7 | Polimento de UI | concluído |

## Log

### Fase 1 — Scaffolding
- pyproject.toml com setuptools, entry point `hugo-tagger`, deps de prod e dev
- Estrutura `src/hugo_tagger/` com `tui/` e `tests/fixtures/`
- venv criado em `.venv/`, pacote instalado em modo editable
- Python 3.14.3, requires-python >= 3.11
- Wrapper em `~/.local/bin/hugo-tagger` para acesso global

### Fase 2 — Módulos core
- **engines.py**: leitura de `~/.hugo-tagger/engines.toml`, criação automática de default, lookup de API key via env var, detecção de motor local (localhost, 127.0.0.1, redes privadas RFC 1918), persistência da última engine selecionada em `last_engine.json`
- **state.py**: state em `~/.hugo-tagger/state/<hash>.json`, mark_processed, get_last_processed
- **normalizer.py**: lowercase, hífens, remoção de artigos (PT/EN/ES/FR), truncamento 3 palavras, dedup contra pool e existing. Acentos preservados.
- **scanner.py**: load_posts (YAML only, TOML ignorado com aviso), collect_tag_pool com frequência, format_pool_for_prompt com limit, find_duplicate_tags (SequenceMatcher + prefixo)
- **writer.py**: write_tags (substituição completa), write_summary (sobrescreve description), sort_keys=False, adiciona lastmod se ausente, reordena metadata (description penúltimo, tags último)
- **llm.py**: prompt de tags (com preservação de acentos), prompt de sumário (140-160 chars), estimativa de tokens (len/4), truncamento de posts longos, parse robusto (strip fences, json.loads, fallback regex), call_llm async com httpx.AsyncClient

### Fase 3 — CLI
- **cli.py**: click command com directory (default $PWD), --batch (default 0 = todos), --report, --engine, --model
- Posts ordenados por data de publicação (mais recentes primeiro)
- Modo report com contagem por status, tags únicas, detecção de duplicatas

### Fase 4 — TUI
- **app.py**: HugoTaggerApp, lança ReviewScreen diretamente
- **review.py**: DataTable com zebra stripes (60/40 split), spinner animado via set_interval + update_cell, máquina de estados (BROWSING/LOADING/REVIEWING), keybindings (t=Tags, s=Summary, e=Engine, q=Quit, Escape=Back), tags existentes com checkboxes editáveis, sumário com current vs suggested + contagem de chars, pool atualizado dinamicamente, painel refresha após Apply
- **engine_picker.py**: modal com dois níveis (engines → modelos), consulta /v1/models do endpoint, filtra embeddings, Escape volta ao nível anterior, seleção persistida
- **selection.py**: legado (não usada, substituída pelo DataTable na review)

### Fase 5 — Testes
- 54 testes, todos passando
- Cobertura: normalizer (9), scanner (10), llm (11), state (5), engines (9)
- Fixtures: post-with-tags, post-no-tags, post-toml-frontmatter, post-draft

### Fase 6 — Iterações pós-protótipo
- Corrigido: detecção de redes privadas (192.168.x.x) como local
- Corrigido: import `work` do Textual (textual.work, não textual.app.work)
- Corrigido: `call_from_thread` não existe no Textual 8.x (async worker não precisa)
- Mudado: PostRow customizado → DataTable nativa (resolveu bug de filename sumindo)
- Adicionado: spinner animado na coluna de status da DataTable
- Adicionado: metadados (título + descrição) no painel direito
- Adicionado: tags existentes com checkboxes editáveis (remoção de tags)
- Adicionado: writer substituição completa (write_tags) em vez de append
- Adicionado: geração de sumário via LLM (tecla 's')
- Adicionado: modal de seleção de engine com listagem dinâmica de modelos
- Adicionado: persistência da engine selecionada entre sessões
- Adicionado: mensagens de erro detalhadas (ConnectError, Timeout, HTTP status, parse error)
- Adicionado: instrução explícita no prompt para preservar acentos
- Adicionado: input de tags manuais (comma-separated, normalizado, aparece só no modo tags)
- Removido: flag --all e SelectionScreen (todos os posts carregados por padrão)
- Removido: botão Sair (q resolve)
- Removido: rich-click → click puro
- Mudado: interface TUI em inglês
- Mudado: proporção dos painéis 60/40

### Fase 7 — Polimento de UI
- Metadados do frontmatter exibidos como Rich Table (duas colunas: campo bold+accent, valor normal)
- Zebra stripes na Rich Table de metadados (background #808080 em linhas alternadas)
- description sempre penúltimo e tags sempre último na exibição e no frontmatter salvo
- Writer reordena metadata automaticamente via _reorder_metadata()
- Removido status-label redundante (hints agora só no Footer)
- Erros do LLM via self.notify() com severity="error" (toast vermelho)
- Coluna de spinner reduzida a 1 char de largura
- Focus automático no botão Apply quando LLM retorna resultado
- Input de tags manuais e botões Apply/Skip colados logo após checkboxes (sem espaço morto)
- tags-panel e suggested-tags-container com height:auto (conteúdo flui sem gaps)
- Corrigido: post.metadata["tags"] atualizado junto com post.tags após Apply (Rich Table refletia dados antigos)
- Adicionado: keybinding 'v' para abrir o post no vim (suspende TUI, recarrega frontmatter ao voltar, oculto do footer)
