# Arquitetura de integrações — Fase 3

> Robustez de execução, inbox, retry, scheduler e replay: consulte
> `docs/integrations-robustness-phase4.md`.

## Fluxo canônico

```text
ERP / arquivo / agente / webhook
        ↓
Connector (transporte por capability)
        ↓
Adapter (mapeamento do formato externo)
        ↓
NormalizedProduct | StockUpdate
        ↓
ProductSyncService | apply_stock_sync
        ↓
Products + ExternalEntityMapping + SyncExecution
```

Connectors não escrevem no banco e adapters não conhecem SQLAlchemy. O tenant
sempre vem da integração autenticada/configurada, nunca do payload externo.

## Capabilities atuais

- `products`: cadastro completo idempotente, com criação automática, atualização
  e correlação por ID externo; SKU canônico é o fallback.
- `stock`: atualização enxuta de produtos existentes; os quatro canais atuais
  (`agent`, arquivo, webhook e REST pull) produzem `StockUpdate` e reutilizam o
  mesmo motor.

## Como adicionar um ERP

1. Implementar `ProductConnector` e/ou `StockConnector` para o transporte.
2. Implementar o adapter correspondente ou reutilizar `MappingProductAdapter`
   e `MappingStockAdapter` com paths configuráveis.
3. Registrar connector e adapter no `IntegrationRegistry`, usando o mesmo nome
   de provider e a capability apropriada.
4. Persistir o nome do connector e sua configuração não sensível em
   `integration_configurations`.
5. Persistir segredos cifrados em `integration_credentials`; chaves de máquina
   pertencem a `integration_api_keys`.

O connector novo não deve duplicar idempotência, isolamento, criação de produto,
aplicação de estoque, histórico ou cache. Essas responsabilidades permanecem nos
serviços compartilhados.

## Identidade e SKU

- `products.normalized_sku` é único por tenant.
- O mesmo SKU pode existir em tenants diferentes.
- `external_entity_mappings` correlaciona
  `(integration_id, entity_type, external_id)` com o ID interno.
- O ID externo tem prioridade; o SKU canônico é usado quando ainda não existe
  mapping, permitindo adotar produtos previamente cadastrados.

## Compatibilidade de dados

`NormalizedProduct` já aceita campos como EAN, atributos, variações, peso e
dimensões. O `ProductSyncService` persiste somente os campos suportados atualmente
pela tabela `products`; os demais permanecem no contrato até a evolução específica
do catálogo, sem contaminar o modelo interno com formatos de ERP.
