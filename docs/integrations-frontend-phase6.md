# Fase 6 — Frontend de integrações

## Contrato e estrutura

O frontend mantém a feature em `src/features/integrations`, separada em API validada, contratos gerados, schemas Zod, componentes e páginas. O comando `npm run generate:api` lê o OpenAPI da API local e atualiza `generated/openapi.ts`.

Os tipos TypeScript vêm do OpenAPI. Toda resposta usada pela feature passa também por um schema Zod antes de entrar no estado React; uma resposta incompatível falha de forma explícita em vez de contaminar a interface.

## Fluxos disponíveis

- listagem e dashboard operacional;
- página de detalhe por integração;
- edição de nome e ativação/desativação;
- configuração REST com modos explícitos `keep`, `replace` e `clear`;
- teste de conexão e dry-run usando o formulário atual sem persistência;
- mapeamento de estoque e produto;
- sincronização por capability e sincronização completa encadeada (produtos antes de estoque);
- histórico paginado, polling de runs ativos, detalhes redigidos e replay;
- setup de webhook com URL, headers, eventos e rotação de segredo;
- gestão da chave do agente;
- invalidação local e cross-tab de catálogo, vitrine e carrinho após transição terminal de um run.

## Endpoints adicionados

- `GET/PATCH /api/v1/integrations/{integration_id}`;
- `POST /api/v1/integrations/{integration_id}/sync-all`;
- `GET /api/v1/integrations/{integration_id}/runs`;
- `GET /api/v1/integrations/{integration_id}/runs/{run_id}`;
- `POST /api/v1/integrations/{integration_id}/api-config/dry-run`.

O teste de conexão aceita opcionalmente `ApiPullConfigIn`; quando enviado, a configuração é construída somente em memória e descartada ao final. Teste e dry-run exigem simultaneamente `integrations:run` e `integrations:secrets`.
