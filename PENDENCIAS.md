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
- `engines.py`: `DEFAULT_ENGINES` com um endpoint local `localhost:5555` e o modelo `apple-ai`. Esse endpoint é do Apple Intelligence via Apfel, não do LM Studio.
- `linker.py` (`_is_affiliate_url`) e `affiliates.py` (exemplo `amzn.to`): afiliados só da Amazon; o atalho `z` insere link da Amazon.
- `hugo.py`: `DEFAULT_PERMALINK = "/:section/:slug/"` (o blog usa `/:slug/`); leitura de `.pages.yml` para categorias; mensagens que citam "PagesCMS" em `writer.py`, `drafting.py` e `draft_screens.py`.
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

### 10. Itens migrados do beads (rascunhos por LLM e dependências)

O projeto deixou de usar o beads. Estes eram os itens abertos nele; o texto abaixo é o registro completo.

- **Rascunhos (`h`/`R`): conjunto de avaliação** (era `hugo-tagger-3a7`, P2, criado em 2026-10-10). Conjunto fixo de pedidos e um motor fixo para comparar as métricas de rascunho antes e depois de mudar o prompt. Adiado porque o usuário decidiu manter os prompts atuais por enquanto.
- **Rascunhos (`h`/`R`): detectar tiques de escrita de IA** (era `hugo-tagger-7a5`, P2, 2026-10-10). Medir no código travessões por mil palavras, o padrão "não X, é Y" e a proporção de frases curtas, e avisar no rascunho. Adiado pelo mesmo motivo.
- **Rascunhos (`h`/`R`): reduzir as amostras de escrita** (era `hugo-tagger-d34`, P2, 2026-10-10). Usar 2 a 3 posts, truncados, para caber no contexto de modelos fracos. Adiado pelo mesmo motivo.
- **Rascunhos (`h`/`R`): checar o tamanho dos rascunhos** (era `hugo-tagger-mi2`, P2, 2026-10-10). Para rascunhos novos, se a saída ficar abaixo de ~70% do alvo, pedir expansão uma vez. Adiado pelo mesmo motivo.
- **Rascunhos (`h`/`R`): simplificar o prompt de refatoração** (era `hugo-tagger-wtf`, P2, 2026-10-10). O prompt ainda está longo. Simplificar só se não trouxer de volta as falhas que ele corrige. Adiado pelo mesmo motivo.
- **Atualizar dependências travadas** (era `hugo-tagger-va3`, P4, criado em 2026-10-07, bloqueado). Estado: `transformers` 4.57.6, `sentence-transformers` 5.7.0, `huggingface-hub` 0.36.2, `tokenizers` 0.22.2 e `optimum` 2.1.0 estão travados (últimas versões: 5.19.0, 6.1.0, 2.1.1, 0.23.2 e 2.3.0). Causa: `optimum-onnx` 0.1.0 exige `optimum~=2.1.0` e `transformers<4.58`, e `src/hugin/embeddings.py` (linha ~176) usa `SentenceTransformer(backend='onnx')`, que depende dele. Opções: (1) esperar um `optimum-onnx` que suporte transformers 5, a opção segura; (2) trocar para `backend='torch'` e remover `optimum`, `optimum-onnx` e `onnx`, medindo antes tempo de carga, velocidade e memória; (3) usar onnxruntime direto, reescrita que não vale a pena. Também: o `uv.lock` está desatualizado porque o `secrets-resolver` deixou de ser pacote local (`../jano`) e hoje vem da tag git; `uv sync` e `uv lock` falham. Para recriar o ambiente, o registro antigo sugeria `uv venv --clear && uv pip install -e ../jano -e '.[dev]' --upgrade`, mas esse comando não reflete mais a dependência por tag: revisar antes de usar. Testar qualquer upgrade num venv separado primeiro.

### 11. Pesquisa web nos rascunhos (`h`/`R`, inspirada no Koala): implementada, falta validar o briefing em pautas reais
- **Estado:** implementada em 2026-11-11 (`src/hugin/research.py`, telas `ResearchScreen` e `BriefReviewScreen` em `tui/draft_screens.py`). Opção "Research the web first" no formulário de `h`/`R`: busca no Brave (5 resultados), leitura de até 3 páginas pelo próprio Hugin, briefing escrito pela engine (intenção de busca, tópicos, perguntas, lacunas, fatos com fonte, estrutura), revisão editável e envio ao escritor. Registro de cada pesquisa em `~/.hugin/research/<rascunho>.md`.
- **Chave:** no Jano em `brave.search`. Busca real testada em 2026-11-11: 5 resultados, 3 páginas lidas; páginas do Reddit não são legíveis sem JavaScript e ficam como "snippet only".
- **Para validar:** rodar em 3 a 5 pautas reais (pt e en) e avaliar: a consulta derivada pela engine é boa? As páginas lidas são úteis ou são lojas e agregadores? O briefing inventa fatos ou cita fontes inexistentes (a tela avisa)? O rascunho mantém a voz e linka as fontes? Ajustar `N_RESULTS`, `N_PAGES` e `PAGE_CHARS` conforme o resultado.
- **Primeiro teste real (2026-11-11, Amo Meu Gato, British Shorthair, `openai/gpt-5.6-terra`):** a reforma corrigiu fatos, mas cortou o que as fontes não citavam (HCM), perdeu 4 links internos e 1 afiliado, ficou defensiva e não identificou de qual fonte vinham alguns números. Corrigido no mesmo dia: na reforma o que as fontes não contradizem fica; cada fato diz de qual fonte vem; ressalva genérica no máximo uma vez; a seção "Gaps" do briefing pede fatos ausentes, não ressalvas; a reforma herda `image` (e outros campos de capa); uma conferência por texto lista imagens, links e tamanho perdidos e pergunta antes de salvar. Rodar de novo o mesmo post para comparar.
- **Lista de afirmações na reforma (implementada em 2026-11-11):** no `R`, a opção vira "Fact-check the original on the web first" (`src/hugin/factcheck.py`, telas `FactCheckScreen` e `ClaimsReviewScreen`). Teste real no original do British Shorthair, sem escrever o rascunho: 12 afirmações, 34 fontes, 26 lidas, cerca de 55 s. A HCM ficou como "keep" citando a Cornell; preço, Churchill/Nelson, Gato de Cheshire e Tom & Jerry foram para "correct". **Falta:** rodar o `R` completo nesse post e comparar com a análise anterior. **Não coberto:** o pedido do `R` que acrescenta conteúdo novo (não é afirmação do original) não é pesquisado; para isso, hoje só no `h`. Alguns sites com proteção anti-bot (ex.: Catster) respondem 403 e ficam como "snippet only".
- **Revisão das afirmações virou opcional (2026-11-11):** a pedido do usuário, as decisões da checagem vão direto ao escritor; a tela de revisão só aparece com "Review the decisions before writing" marcado, ou quando a checagem falha. O briefing do `h` seguiu o mesmo caminho ("Review the brief before writing", desmarcado por padrão).
- **Verificação do rascunho antes de salvar (implementada em 2026-11-11, `src/hugin/verify.py`):** links externos que não vêm do original nem das fontes (sem LLM); afirmações sem apoio nas referências, contra decisões da checagem e atribuições erradas (segunda chamada à engine, só quando há referências). Teste real no rascunho antigo do British Shorthair contra o original, sem as fontes: apontou 2 afirmações novas (genética do "azul", comparação com braquicefálicos), mas não apontou os dados atribuídos à Husse e à Purina, cujos textos não estavam disponíveis. Ou seja, deixa passar coisas. Avaliar no próximo `R` completo, agora com as fontes no prompt. **Não coberto:** links internos inventados (URL de post que não existe).
- **Correção automática após a verificação (2026-11-21, `verify.check_and_fix`):** em vez de só listar, o Hugin corrige: links externos desconhecidos viram texto (sem LLM) e uma terceira chamada à engine reescreve cada trecho problemático a partir das referências (corrige, generaliza ou remove). A tela de checagem só aparece com o que sobrou, mostrando também o que já foi corrigido; as correções vão para o log em `~/.hugin/research/`. **Avaliar no uso real:** se a engine copia o trecho (`find`) fielmente (senão o problema fica em aberto) e se as remoções não deixam o texto truncado. Não há segunda verificação depois da correção.
- **Experiências inventadas (2026-11-21):** o redator imitava a voz dos exemplos e inventava vivências do autor ("depois de destruir minha caneta tinteiro…"), e a verificação ignorava "the author's own experience" de propósito. Agora o prompt do redator proíbe isso e a verificação tem o tipo `personal`, conferido contra o pedido (e o original, num refactor); posts novos sem pesquisa também passam por essa checagem (só ela). **Avaliar no uso real:** se a checagem não marca como inventada uma experiência que você contou no pedido com outras palavras.
- **Tags longas (resolvido em 2026-11-11):** tags com mais de 3 palavras agora são recusadas, não cortadas: as sugestões do `t` saem da lista com um aviso, e tags digitadas à mão ou renomeadas no gerenciador são barradas até serem encurtadas.
- **Não feito (escolhas abertas):** análise de posts existentes contra a concorrência sem reescrever; escolha do tipo de busca (News, Scholar); pontuação de cobertura de termos. A leitura de página é um extrator HTML simples, sem JavaScript: páginas que só renderizam no navegador viram "no readable text".

### 12. Marcadores de imagem `[img]` (implementado em 2026-11-22, falta validar no uso real)
- **O que faz:** o prompt "Draft · image placeholders" pede ao redator (`h`/`R`) marcadores `[img]descrição[/img]` em linha própria, descrição em inglês, terminando em "Aspect ratio 16:9.", no máximo um por seção. `I` lista os marcadores do post e Enter copia a descrição. O `g` pergunta antes de remover marcadores de posts publicados; recusar cancela o sync. Rascunhos (`draft: true`) ficam com os seus. Código (cercas e inline) é ignorado. Módulo: `src/hugin/placeholders.py`.
- **Avaliar:** quantos marcadores o modelo gera e se as descrições servem para o ChatGPT/Midjourney; se o Midjourney precisa de `--ar 16:9` em vez do texto.
- **Também pendente (imagens no Typora):** testar a colagem de imagem com `typora-copy-images-to` (o link deve sair `/wp-content/uploads/<post>/...`). No amo-meu-gato, o post da anemia tem link quebrado no commit `3095e14b` e alterações locais não commitadas; a imagem `static/wp-content/uploads/anemia/Winged-Minimessenger-Chat-Bubble.png` está órfã. Decisão do usuário.

### 13. Números e estatísticas inventados (implementado em 2026-11-22, falta validar num `R` real)
- **Regra única:** detalhe preciso (estatística, porcentagem, contagem, data, recorde, estudo, citação) só com referência; o original de um refactor não conta. Sem referência, o ponto fica geral ou sai.
- **Onde:** prompt novo "Draft · precise figures" no redator; UNCONFIRMED da checagem perde o número; tipo `figure` na verificação, também no `h` sem pesquisa; `verify.unsourced_numbers` (sem LLM) aponta à verificação as frases com números ausentes das referências (decisões UNCONFIRMED/REMOVE não contam).
- **Avaliar:** num `R` de post antigo cheio de números, se sobra número sem fonte e se o texto não fica genérico demais; se números inofensivos (lista, anedota) são mantidos.

### 14. Check-list editorial do Amo Meu Gato (implementado em 2026-11-23, falta configurar e validar)
- **Feito:** regras nos prompts (título/descrição sem clickbait, resposta no início, sem enchimento, "estudos mostram" proibido, informação datada, opinião marcada, saúde), checagens novas ("vague", "health"), links mantidos do original abertos e conferidos, fontes confiáveis/excluídas, imagens (largura e alt no `g`), "Already on the blog?" no `h`, `keep_url`, `lastmod` só com mudança real, links nunca para posts substituídos, fusão de posts.
- **Falta configurar no Amo Meu Gato (`,`):** marcar "keep the original's address and date"; preencher as fontes confiáveis (cornell.edu, icatcare.org, catvets.com, wsava.org, cfmv.gov.br…); largura mínima 1200; `redacao-amg` como primeiro autor; regras editoriais do blog.
- **Não dá pelo Hugin:** garantir que a imagem gerada bate com a raça (só revisão humana); IPTC `DigitalSourceType` (o Hugin não converte nem grava imagens: isso cabe à ferramenta de imagens); "marcado para remover" (não existe essa marca; hoje só refactor/merge marcam um post como substituído).
- **Tamanho é guia (2026-11-23):** o tamanho pedido no `h` e o do original no `R` são referência, não meta. Só um refactor abaixo de 60% do original (ou uma fusão abaixo de 60% do post mais longo) gera aviso de possível perda de conteúdo.

### 15. Sincronização automática desligada (temporário, desde 2026-11-24)
- **Estado:** durante a grande refatoração do Amo Meu Gato, o Hugin só mexe no repositório do blog com o `g`. Saíram as duas sincronizações do início (`cli.py`: commit, pull e push ao abrir e depois das migrações) e o commit com push ao salvar as configurações do projeto (`save_and_sync` em `tui/project_settings.py` agora só grava o arquivo; as configurações vão no próximo `g`).
- **Continua igual:** a migração de keywords pode fazer um commit local (sem push), mas só roda com a árvore limpa, então não age enquanto houver posts alterados.
- **Para desfazer quando a refatoração terminar:** `git revert` do commit "Touch the blog repository only on 'g'". `commit_settings` e seus testes continuam em `git.py`, então o revert volta tudo como estava.

## Projeto separado (a tratar em outra sessão)

- **Amo Meu Gato:** projeto diferente, a ser tratado em sessão própria. Nada deste arquivo se aplica a ele. Ao retomar, informar o repositório e o contexto do projeto antes de qualquer mudança.

## Estado do git ao salvar este arquivo

- `hugo-tagger`: `main` sincronizado com `origin`. A pasta `.beads/` foi removida do repositório e do disco.
- Este arquivo (`PENDENCIAS.md`) não foi commitado.
