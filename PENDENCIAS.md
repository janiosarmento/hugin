# Pendências

Snapshot feito ao fim da sessão de 2026-10-12. Cada item diz o estado, o que falta e o que decidir. Itens de Echo também estão no beads (`bd list --status=open`); este arquivo cobre o resto.

Como retomar numa sessão nova: ler este arquivo, `CLAUDE.md` e `PROJECT.md`, depois `git log --oneline -10` para ver os últimos commits.

## Retomar do zero

- **Repositório do Hugin:** `~/projects/hugo-tagger` (branch `main`, sincronizado com `origin`, último commit `cff6fc5`).
- **Ambiente:** `.venv` com Python 3.13. Testes: `.venv/bin/python -m pytest -q -p no:cacheprovider` (385 passando). Lint: `ruff check src/hugin tests` (limpo).
- **Blog:** `~/projects/blogue-do-janio` (repositório separado). Diretórios de posts: `content/pt/post` e `content/en/post`. Último commit de keywords: `9c28e60`, já no remoto.
- **Jano (dependência `secrets-resolver`):** `~/projects/jano`. Tag `v0.7.0` publicada no GitLab. O Hugin aponta para essa tag em `pyproject.toml`.
- **Estado local (fora dos repositórios):** `~/.hugin/` (engines, perfis de links em `embeddings/*_kw.json`, estado em `state/`, log em `hugin.log`) e `~/.config/helix/` (`config.toml` com `evil = true`, `theme = "dark-synthwave"`, régua em 80 colunas; `languages.toml` com soft-wrap para Markdown).
- **Ferramentas:** `hugin` no PATH; `hx` (evil-helix 25.07.1); `$EDITOR` precisa estar exportado para o atalho `E` funcionar.

## Pendências do Hugin

### 1. Migração para Python 3.15 (bloqueada)
- **Estado:** bloqueada. Em 2026-10-11, `torch` (2.14.1) e `onnxruntime` (1.31.0) não tinham wheels `cp315` no PyPI. Sem eles, `sentence-transformers[onnx]` não resolve.
- **O que o resto já aceita:** os 385 testes passam em 3.15 com as dependências disponíveis e o `secrets-resolver` pinado.
- **Quando desbloquear, fazer tudo de uma vez:** trocar `requires-python` para `>=3.15` (ou manter `>=3.11`, decidir); recriar o `.venv` em 3.15; regenerar o `uv.lock` (ele não tem `secrets-resolver`, que hoje vem da tag git); rodar a suíte completa, incluindo `test_embeddings_rank` e o código de embeddings, que não rodam sem torch; atualizar a menção de versão no README e no PROGRESS.
- **Checar:** `uv pip compile` com `--python-version 3.15` para torch e onnxruntime.

### 2. Qualidade das âncoras de links (implementado, não validado com o modelo real)
- **Estado:** commit `cff6fc5`. O LLM escolhe as âncoras a partir das keywords (link profile) do post alvo. O casamento pelo slug e as listas de stopwords foram removidos.
- **Decisões a confirmar:**
  - Candidatos **sem link profile são pulados**. Hoje são 18 posts pt e 3 en (ver item 4).
  - **Não há mais fallback pelo slug**. Se o LLM não devolver âncora, o candidato não gera link.
- **Para validar:** rodar `o` em alguns posts reais e comparar com as âncoras anteriores. Modelos de raciocínio tendem a falhar na exigência de texto verbatim (o README recomenda modelos de instrução).

### 3. Atalhos do Helix
- **Pendente (decisão do usuário):** os atalhos sugeridos no Helix ("jk" para sair do insert, mapeamento para salvar). O usuário quer ver depois.
- **Não queridos:** números de linha relativos.

### 4. Perfis de link faltando
- **18 posts pt** e **3 posts en** não têm link profile e, portanto, não têm keywords nem entram como candidatos.
- **Ação:** `hugin-profiles ~/projects/blogue-do-janio/content/pt/post` (e `content/en/post`), depois abrir o Hugin para preencher as keywords.
- **Atenção:** `hugin-profiles` ignora drafts por padrão (`--drafts` inclui). Posts recém-criados ficam sem perfil até serem publicados ou gerados com `o`.

### 5. Decisões sobre rascunhos
- **Decidido:** manter o comportamento atual. `hugin-profiles` ignora drafts; o perfil de um rascunho sai com `o`. O usuário confirmou.

### 6. Tradução para inglês (concluída, com exceções)
- **Feito:** comentários, docstrings, mensagens de CLI e de erro, e fixtures sem dependência de idioma (commit `c2765c6`).
- **Mantido de propósito:**
  - stopwords de PT/ES/EN em `normalizer.py` (dados de detecção de idioma);
  - `test_linker.py` e as linhas de acento de `test_normalizer.py` (testes de comportamento em português);
  - exemplos em português nos prompts de `llm.py` (linha 26, acentuação; linha ~316, exemplo de expressão composta);
  - palavras emprestadas como "café".
- **Decidir:** traduzir ou não os exemplos de prompt.

### 7. Generalização do código (auditoria feita, nada alterado)
Itens específicos do uso do autor encontrados no código:
- `engines.py`: `DEFAULT_FULCRUM_ECHO_PERSONA = "Janio Sarmento"`, segredo padrão `fulcrum_echo.key` e bloco `[fulcrum_echo]` escrito no `engines.toml` de todo usuário.
- `engines.py`: `DEFAULT_ENGINES` com um endpoint local `localhost:5555` e o modelo `apple-ai`.
- `linker.py` (`_is_affiliate_url`) e `affiliates.py` (exemplo `amzn.to`): afiliados só da Amazon; o atalho `z` insere link da Amazon.
- `hugo.py`: `DEFAULT_PERMALINK = "/:section/:slug/"` (o blog usa `/:slug/`); leitura de `.pages.yml` para categorias; mensagens que citam "PagesCMS" em `writer.py`, `echo.py` e `echo_draft.py`.
- `redirects.py`: supõe `static/_redirects`. `git.py`: `SETTINGS_PATHS` supõe `hugin/config.toml` e `data/authors.toml`.
- `project.py`: `DEFAULT_AUTHOR_FIELD = "author"`.
- Testes: fixtures com exemplos de gatos (`cat food`, `litter box`, `arranhador`).
- **Decidir:** quais valores viram configuração e quais saem do código. Ver também a regra do `CLAUDE.md` sobre código em inglês.

### 8. Migração de keywords: limitações conhecidas
- Roda em **todo startup** sobre o diretório aberto. Posts sem o campo `keywords` e com perfil são preenchidos de novo, mesmo que o usuário tenha apagado o campo de propósito. Foi uma decisão do usuário.
- Só preenche se o repositório não tiver alterações em arquivos rastreados; caso contrário, avisa e não faz nada.
- Só lê posts de **um diretório por vez**. Os posts em inglês precisam de `hugin content/en/post`.
- `k` substitui as keywords atuais pelo perfil, sem perguntar.

### 9. Repositório do Jano (não é deste trabalho)
- Há três arquivos apagados sem commit em `.recall/` (`.capture.json`, `context.md`, `history.md`). Não foram tocados. Decidir se são do usuário e o que fazer com eles.
- As tags `v0.5.22`, `v0.5.23` e `v0.5.24` nunca foram criadas; só existem os commits de bump.

### 10. Itens do beads (Echo e outros)
- `hugo-tagger-3a7`: conjunto de avaliação fixo para comparar métricas de rascunho antes e depois de mudar o prompt.
- `hugo-tagger-7a5`: detectar tiques de escrita de IA (travessões por mil palavras, padrão "não X, é Y", razão de frases curtas) e avisar no rascunho.
- `hugo-tagger-d34`: reduzir os exemplos de escrita para 2 a 3 posts, truncados, por causa do contexto de modelos fracos.
- `hugo-tagger-mi2`: checar o tamanho dos rascunhos novos e pedir expansão uma vez se ficar abaixo de ~70% do alvo.
- `hugo-tagger-wtf`: o prompt de refatoração ainda está longo; simplificar só se não trouxer de volta as falhas que ele corrige.
- `hugo-tagger-va3` (P4, bloqueado): revisitar upgrades de dependência travados pelo pin do `optimum-onnx`.

## Projeto separado (a tratar em outra sessão)

- **Amo Meu Gato:** projeto diferente, a ser tratado em sessão própria. Nada deste arquivo se aplica a ele. Ao retomar, informar o repositório e o contexto do projeto antes de qualquer mudança.

## Estado do git ao salvar este arquivo

- `hugo-tagger`: `main` sincronizado com `origin`. `.beads/issues.jsonl` ficou com alterações do beads depois do último commit; decidir se entra no próximo commit.
- Este arquivo (`PENDENCIAS.md`) não foi commitado.
