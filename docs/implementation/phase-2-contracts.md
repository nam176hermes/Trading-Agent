# Phase 2 Contracts

Strict Pydantic 2 contracts cover Asset, MarketAssetSnapshot, MarketReport, Signal, DecisionSignals, DecisionRecord, DecisionSummary, SystemStatus, DataFreshness, CapabilityEvidence, CostSummary, DeploymentMeta, pagination, health, and versioned success/error envelopes.

Canonical enums include freshness, capability evidence, execution mode/capability, asset class, kill-switch state, risk, confidence, cost-evidence quality, and decision actions. Domain construction rejects wrong scalar types and out-of-range confidence. Legacy adapters explicitly normalize missing numeric fields, action spellings, and confidence into canonical values.

Schema version is `1.0.0`. OpenAPI, JSON Schema, TypeScript, and Zod outputs are generated deterministically. Static TypeScript types come from OpenAPI; runtime payloads must pass a generated Zod schema before use.
