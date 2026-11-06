# Agente neste repositório

**Escopo:** este repositório é só o **aplicativo de chat** (React + FastAPI + SQLite). A **inferência** roda à parte no projeto **[Nook](https://github.com/kadupenido/nook)** — API compatível com OpenAI (ex.: `http://127.0.0.1:9080/v1`), com autenticação **Bearer** (`LLAMA_API_KEY`).

O navegador só fala com o FastAPI. O FastAPI guarda a conversa, monta o contexto e faz proxy para esse upstream. Preserve o contrato abaixo. Variáveis e curl ficam no README.

## Mapa

- `frontend/` — React. O stream é lido com `fetch` em `frontend/src/api/sse.ts`.
- `backend/app/` — API, contexto, cliente do modelo e persistência.
- `backend/app/services/chat.py` — `complete` e `generate_stream`; com `web_search_enabled`, delega ao agente.
- `backend/app/services/agent.py` — loop de tools (`stream_with_tools`, `complete_with_tools`).
- `backend/app/services/tools/web_search.py` — schema da tool `web_search` e chamada Tavily.
- `backend/app/services/attachments.py` — extração de texto, composição do prompt e ligação de anexos.
- `backend/tests/` — pytest. O modelo falso é `FakeLLM` em `backend/tests/conftest.py`.

## Contexto

O system prompt e a mensagem atual sempre entram. O histórico entra do mais novo para o mais antigo, só em pares `user`/`assistant` cuja resposta está `complete` (`backend/app/services/context.py`). O primeiro par que não cabe corta os mais antigos: não abra um buraco no meio da conversa.

Mensagens `interrupted` e `error` ficam no banco e na tela, e ficam fora do prompt. Um `user` sem resposta `complete` depois dele também fica de fora.

Uma mensagem que não cabe no orçamento responde 413 (`MessageTooLong`). O mesmo 413 vale quando o **upstream** recusa o prompt por estouro de contexto. Não corte a pergunta em silêncio. Se algum turno antigo ficou de fora, a resposta traz `history_truncated: true`.

A ocupação do prompt é a soma de `caracteres / CHARS_PER_TOKEN` mais `MESSAGE_OVERHEAD_TOKENS` por mensagem que entrou (`prompt_token_estimate`). O `meta` traz essa estimativa em `context_tokens` e a janela em `context_limit` (`LLAMA_CTX_SIZE`). O GET da conversa devolve a mesma estimativa sobre o system prompt e os pares `complete`. No `done`, se o upstream mandou usage, `context_tokens` passa a ser `prompt_tokens + completion_tokens`. A barra lateral mostra esse total, o percentual sobre `context_limit` e a taxa de geração.

## Geração

Não há retry automático: repetir a pergunta gravaria outro turno. "Tentar novamente" manda `regenerate: true` e reusa a última mensagem do usuário.

Abortar o POST grava o texto parcial com status `interrupted`.

Blocos entre os delimitadores tratados por `ThinkStripper` (`backend/app/services/think.py`) e o campo `reasoning_content` não entram em `content`, na bolha markdown nem no histórico do prompt (`backend/app/services/think.py`, `backend/app/services/llm.py`). Com `LLAMA_ENABLE_THINKING=true`, o stream expõe o raciocínio em eventos `think` e no campo `thinking` do `done` (só na sessão da UI; não persiste no SQLite). O padrão é `LLAMA_ENABLE_THINKING=false`. Com thinking desligado, o backend manda `chat_template_kwargs.enable_thinking=false` e descarta o bloco se o modelo ainda o emitir (se o upstream suportar esse extra body).

Uma conversa que já está gerando responde 409. Capacidade de geração simultânea neste processo esgotada responde 503. `LLAMA_PARALLEL` limita quantas gerações podem correr ao mesmo tempo **neste backend**; alinhe com a capacidade do upstream.

O backend só envia `temperature` quando o cliente pede, e sempre envia `max_tokens` (o valor do pedido ou `MAX_TOKENS_DEFAULT`). Outros parâmetros de amostragem dependem do upstream.

O `done` traz `prompt_tokens`, `completion_tokens` e `tokens_per_second` quando o usage chega. A taxa é `completion_tokens` dividido pelo tempo desde o primeiro token visível até o fim do stream. Sem usage, os três ficam nulos e a tela conserva a contagem dos eventos `token`.

## Título e prompt

`SYSTEM_PROMPT` é copiado para a conversa na criação. Mudar a variável não reescreve conversas antigas.

O título provisório é o começo da primeira mensagem, até 48 caracteres (`backend/app/services/titles.py`). No stream ele segue no `done`. A chamada curta que tenta substituí-lo só corre depois, se o slot estiver livre, e só grava se o título ainda for o provisório. Um rename manual feito antes permanece. Se a chamada falha, o provisório permanece.

## Segredos e dados

`LLAMA_API_KEY` é o Bearer para o upstream; fica só no backend.

`API_KEY`, quando preenchida, é injetada pelo nginx do frontend ou pelo proxy do Vite. Ela não entra no JavaScript.

SQLite aceita um escritor por vez. O app é para uma pessoa. Não abra vários processos da API no mesmo arquivo.

## Busca na web

Com `WEB_SEARCH_ENABLED=true` e `TAVILY_API_KEY` preenchida, o backend oferece a ferramenta `web_search` ao upstream **desde que** ele suporte function calling no formato OpenAI. O loop agente corre no FastAPI (`agent.py`): cada rodada fala com o modelo em stream; se ele pede a tool, o backend chama a Tavily (`web_search.py`) e devolve `role: tool`; a resposta final do assistente é a única gravada no SQLite. Resposta direta (sem tool) também chega token a token no SSE.

`chat.py` usa `stream_with_tools` no stream e `complete_with_tools` no JSON. O JSON reutiliza o mesmo loop por baixo, mas não expõe eventos SSE de `tool_start` / `tool_end` — só o texto final.

Mensagens `tool` e turnos intermediários do agente não entram no histórico do prompt (`context.py`). O system prompt efetivo inclui instruções extras sobre quando usar a busca (`config.effective_system_prompt`).

O stream SSE traz `tool_start` e `tool_end` (consulta e fontes) além de `meta`, `think`, `token`, `done`, `title` e `error`. O `tool_end` pode incluir `error` quando a Tavily falha. Fontes na UI são efêmeras até o `done`.

Depois de anexar o resultado da tool ao prompt de trabalho, `_enforce_context_budget` em `agent.py` pode lançar 413 (`MessageTooLong`) se o turno não couber mais no orçamento.

Variáveis: `WEB_SEARCH_ENABLED`, `TAVILY_API_KEY`, `WEB_SEARCH_MAX_RESULTS`, `WEB_SEARCH_MAX_CHARS`, `AGENT_MAX_TOOL_ROUNDS`. Esgotar o limite de rodadas no mesmo turno responde 502 (`AppError`: “Limite de chamadas de ferramenta atingido neste turno.”), exposto como evento `error` no stream.

## Upload de arquivos

Com `FILE_UPLOAD_ENABLED=true`, o backend aceita texto/código e PDF em `POST /api/attachments` (multipart). Só o **texto extraído** e o nome do arquivo ficam no SQLite (`message_attachments`); o binário não é guardado.

O `content` gravado na mensagem `user` é só o texto digitado. O modelo vê `compose_user_content` (blocos `Anexo: …` no turno atual e no histórico). A UI mostra chips com o nome do arquivo; o GET da conversa devolve `attachments` em cada mensagem, sem repetir o texto extraído.

Fluxo: upload (cria conversa rascunho se `conversation_id` vier vazio) → `POST /api/chat` ou stream com `attachment_ids` → `prepare` liga os anexos ao `message_id`. Regenerar recompõe a última pergunta com os anexos já ligados. Anexo grande que estoura o orçamento responde 413, como mensagem longa.

Variáveis: `FILE_UPLOAD_ENABLED`, `FILE_UPLOAD_MAX_BYTES`, `FILE_UPLOAD_MAX_FILES`, `FILE_UPLOAD_MAX_CHARS`. O nginx do frontend usa `client_max_body_size` alinhado ao limite de bytes.

## Fora deste recorte

RAG, cadastro e login, embeddings, resumo automático do histórico e testes de browser automatizados.

## Verificação

No backend, `pytest` na pasta `backend/`. O `pytest.ini` exclui o marcador `integration`. No frontend, `npm test`.

O teste de integração e o `docker compose up` só quando a tarefa pede o upstream de verdade. Mudança de tela segue o checklist manual do README.

## Idioma

Textos da interface, erros da API e o README estão em português.

## Manutenção

Uma invariante que mudar no código muda neste arquivo no mesmo commit.
