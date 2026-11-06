# Oficina LLM (cliente de chat)

Este repositório contém **apenas** o aplicativo de **chat**: interface React, API FastAPI (conversas, contexto, SSE, anexos opcionais) e persistência em SQLite.

A **inferência** não roda aqui. Ela fica no projeto separado **[Nook](https://github.com/kadupenido/nook)** — uma API **compatível com OpenAI** (por exemplo `http://127.0.0.1:9080/v1/chat/completions`) com autenticação **Bearer**. O backend deste repo faz o proxy usando `LLAMA_BASE_URL` e `LLAMA_API_KEY`.

## Arquitetura

```
Browser → FastAPI (este repo) → Nook / API OpenAI-compatible (Bearer)
```

Contratos de código estão no [AGENTS.md](AGENTS.md).

## Requisitos

- [Nook](https://github.com/kadupenido/nook) (ou outro servidor compatível) acessível na URL configurada em `LLAMA_BASE_URL`
- Docker Desktop **ou** Python 3.10+ e Node.js 22 para desenvolvimento local

## Configuração

```powershell
copy .env.example .env
```

Edite o `.env` e preencha pelo menos **`LLAMA_API_KEY`** (Bearer obrigatório no upstream). Ajuste **`LLAMA_BASE_URL`** se o Nook não estiver em `http://127.0.0.1:9080/v1`.

`LLAMA_CTX_SIZE` define o orçamento de contexto **deste** backend (histórico + mensagem) e a barra na UI. Alinhe com o limite real do modelo no Nook.

`API_KEY` vazio desliga a autenticação nas rotas `/api/*` **deste** FastAPI. Se preenchida, exige `Authorization: Bearer` para o app. `GET /health` continua aberto. No Compose, o nginx do frontend injeta esse header; a chave não entra no JavaScript. No `npm run dev`, o proxy do Vite faz o mesmo a partir do `.env` da raiz.

## Subir com Docker

Com o Nook já rodando no **host** (ex.: porta 9080):

```powershell
docker compose up --build
```

- Interface: http://127.0.0.1:8088  
- API e Swagger: http://127.0.0.1:8000/docs  

O serviço `backend` usa por padrão `LLAMA_BASE_URL=http://host.docker.internal:9080/v1` para alcançar o Nook no host (Docker Desktop). Em Linux puro, defina `LLAMA_BASE_URL` no `.env` para o IP do host ou use `network_mode: host` conforme seu ambiente.

O SQLite fica no volume `backend-data`.

## Desenvolvimento local

Com o Nook no ar:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

```powershell
cd frontend
npm install
npm test
npm run dev
```

O Vite sobe em http://localhost:5173 e encaminha `/api` e `/health` para o backend em `http://127.0.0.1:8000`. Deixe `VITE_API_BASE_URL` vazio.

## Testar o upstream (curl)

Substitua a URL e o token conforme o Nook:

```powershell
curl.exe -s http://127.0.0.1:9080/health
curl.exe -s http://127.0.0.1:9080/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -H "Authorization: Bearer SUA_CHAVE" ^
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"Diga oi em uma frase.\"}],\"max_tokens\":64}"
```

## API deste repositório

| Método | Caminho | Descrição |
|--------|---------|-----------|
| GET | `/health` | API no ar; `degraded` se o upstream (`GET` na raiz derivada de `LLAMA_BASE_URL`) não responder bem |
| GET/POST/PATCH/DELETE | `/api/...` | Ver Swagger em `/docs` |

## Variáveis de ambiente (resumo)

| Variável | Função |
|----------|--------|
| `LLAMA_BASE_URL` | Base OpenAI-compatible (termina em `/v1`) |
| `LLAMA_API_KEY` | Bearer para o upstream (**obrigatória**) |
| `LLAMA_MODEL` | Id do modelo; vazio = primeiro de `/v1/models` |
| `LLAMA_TIMEOUT_SECONDS` | Timeout HTTP para o upstream |
| `LLAMA_ENABLE_THINKING` | Stream de raciocínio (`think` / `thinking` no `done`) se o upstream suportar |
| `LLAMA_CTX_SIZE` | Orçamento de contexto na UI e no corte de histórico |
| `LLAMA_PARALLEL` | Máximo de gerações simultâneas neste processo |
| `MAX_TOKENS_DEFAULT` | `max_tokens` padrão quando o cliente não envia |
| `MESSAGE_OVERHEAD_TOKENS`, `CHARS_PER_TOKEN`, `CONTEXT_RESERVE_TOKENS` | Estimativa de tokens para o orçamento |
| `WEB_SEARCH_ENABLED`, `TAVILY_*`, `AGENT_MAX_TOOL_ROUNDS` | Agente com busca (só se o upstream suportar tools) |
| `API_KEY` | Proteção opcional das rotas `/api/*` deste backend |
| `UPSTREAM_TIMEOUT_SECONDS` | Timeout do nginx → backend no frontend em Docker |

## Busca na web

Com `WEB_SEARCH_ENABLED=true` e `TAVILY_API_KEY`, o backend oferece a tool `web_search` ao upstream. Isso só funciona se o servidor de inferência suportar function calling no mesmo formato que a API OpenAI.

## Teste de integração

Fala com o Nook de verdade e fica fora do `pytest` padrão:

```powershell
cd backend
$env:LLAMA_BASE_URL = "http://127.0.0.1:9080/v1"
$env:LLAMA_API_KEY = "sua-chave"
.\.venv\Scripts\python -m pytest -m integration --override-ini="addopts="
```

## Verificação

- Backend: `pytest` na pasta `backend/` (suíte padrão exclui `integration`)
- Frontend: `npm test` na pasta `frontend/`

Se o upstream estiver parado, a tela mostra o estado degradado e continua utilizável.
