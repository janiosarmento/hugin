# Pendências

Snapshot feito ao fim da sessão de 2026-10-12. Cada item diz o estado, o que falta e o que decidir. Este arquivo é a única lista de pendências do projeto.

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

### 10. Itens migrados do beads (Echo e dependências)

O projeto deixou de usar o beads. Estes eram os itens abertos nele; o texto abaixo é o registro completo.

- **Echo: conjunto de avaliação** (era `hugo-tagger-3a7`, P2, criado em 2026-10-10). Conjunto fixo de pedidos e um motor fixo para comparar as métricas de rascunho antes e depois de mudar o prompt. Adiado porque o usuário decidiu manter os prompts atuais por enquanto.
- **Echo: detectar tiques de escrita de IA** (era `hugo-tagger-7a5`, P2, 2026-10-10). Medir no código travessões por mil palavras, o padrão "não X, é Y" e a proporção de frases curtas, e avisar no rascunho. Adiado pelo mesmo motivo.
- **Echo: reduzir as amostras de escrita** (era `hugo-tagger-d34`, P2, 2026-10-10). Usar 2 a 3 posts, truncados, para caber no contexto de modelos fracos. Adiado pelo mesmo motivo.
- **Echo: checar o tamanho dos rascunhos** (era `hugo-tagger-mi2`, P2, 2026-10-10). Para rascunhos novos, se a saída ficar abaixo de ~70% do alvo, pedir expansão uma vez. Adiado pelo mesmo motivo.
- **Echo: simplificar o prompt de refatoração** (era `hugo-tagger-wtf`, P2, 2026-10-10). O prompt ainda está longo. Simplificar só se não trouxer de volta as falhas que ele corrige. Adiado pelo mesmo motivo.
- **Atualizar dependências travadas** (era `hugo-tagger-va3`, P4, criado em 2026-10-07, bloqueado). Estado: `transformers` 4.57.6, `sentence-transformers` 5.7.0, `huggingface-hub` 0.36.2, `tokenizers` 0.22.2 e `optimum` 2.1.0 estão travados (últimas versões: 5.19.0, 6.1.0, 2.1.1, 0.23.2 e 2.3.0). Causa: `optimum-onnx` 0.1.0 exige `optimum~=2.1.0` e `transformers<4.58`, e `src/hugin/embeddings.py` (linha ~176) usa `SentenceTransformer(backend='onnx')`, que depende dele. Opções: (1) esperar um `optimum-onnx` que suporte transformers 5, a opção segura; (2) trocar para `backend='torch'` e remover `optimum`, `optimum-onnx` e `onnx`, medindo antes tempo de carga, velocidade e memória; (3) usar onnxruntime direto, reescrita que não vale a pena. Também: o `uv.lock` está desatualizado porque o `secrets-resolver` deixou de ser pacote local (`../jano`) e hoje vem da tag git; `uv sync` e `uv lock` falham. Para recriar o ambiente, o registro antigo sugeria `uv venv --clear && uv pip install -e ../jano -e '.[dev]' --upgrade`, mas esse comando não reflete mais a dependência por tag: revisar antes de usar. Testar qualquer upgrade num venv separado primeiro.

## Projeto separado (a tratar em outra sessão)

- **Amo Meu Gato:** projeto diferente, a ser tratado em sessão própria. Nada deste arquivo se aplica a ele. Ao retomar, informar o repositório e o contexto do projeto antes de qualquer mudança.

## Estado do git ao salvar este arquivo

- `hugo-tagger`: `main` sincronizado com `origin`. A pasta `.beads/` continua no disco, mas o projeto não depende mais dela.
- Este arquivo (`PENDENCIAS.md`) não foi commitado.
