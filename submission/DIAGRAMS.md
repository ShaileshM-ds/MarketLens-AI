# Editable system diagrams

These Mermaid diagrams describe the implemented system. Render with a Mermaid-compatible Markdown viewer or paste each block into Mermaid Live Editor for SVG export.

## Architecture

```mermaid
flowchart LR
  U[Shopper or analyst] --> UI[React / TypeScript / ECharts]
  UI --> API[FastAPI versioned API]
  API --> N[Normalization and analytics services]
  API --> DB[(MongoDB: isolated mode databases)]
  W[Separate Python worker] --> DB
  W --> N
  W --> B[Bright Data provider adapter]
  B --> R[Raw records]
  F[JSON / JSONL / CSV] --> N
  D[Deterministic demo provider] --> N
  N --> R
  R --> DB
```

## Use cases

```mermaid
flowchart LR
  Guest[Visitor] --> Explore[Explore observations]
  Guest --> Time[Reconstruct historical state]
  Guest --> Analyze[Inspect explanations and evidence]
  Consumer[Signed-in consumer] --> Watch[Save own watchlist]
  Consumer --> Alerts[Read own notifications]
  Analyst[Analyst or public read-only visitor] --> Dashboard[Inspect aggregate analytical evidence]
  Admin[Administrator] --> Import[Profile and import]
  Admin --> Collect[Queue capped collection]
  Admin --> Schedule[Manage schedules]
  Admin --> Reset[Reset demo / move simulated clock]
```

## Data-flow diagram: context

```mermaid
flowchart LR
  Source[File / provider / synthetic generator] -->|records and source metadata| ML[MarketLens AI]
  User[User] -->|queries / preferences| ML
  ML -->|observations / explanations / alerts| User
  Admin[Administrator] -->|mapping / caps / schedule| ML
  ML -->|run status / quarantine| Admin
```

## Data-flow diagram: processing

```mermaid
flowchart TD
  Input[Source record] --> Raw[(Raw retention)]
  Input --> Validate[Map, normalize, validate]
  Validate -->|invalid| Quarantine[(Reason-linked rejection)]
  Validate -->|valid| Key[Stable observation key]
  Key -->|already exists| Duplicate[Duplicate count]
  Key -->|new| Observations[(Immutable observations)]
  Observations --> Quality[Cutoff and comparability eligibility]
  Quality --> Analytics[Discount / audit / sellers / analogs / forecast]
  Analytics --> Results[(Versioned analyses)]
  Results --> API[Evidence-linked API]
  Observations --> Worker[Watch condition evaluation]
  Worker -->|new crossing| Notification[(Owner-scoped notification)]
```

## Entity relationships

```mermaid
erDiagram
  PRODUCTS ||--o{ OBSERVATIONS : has
  SELLERS ||--o{ OBSERVATIONS : offers
  RAW ||--o{ OBSERVATIONS : normalizes
  RUNS ||--o{ OBSERVATIONS : ingests
  RUNS ||--o{ QUARANTINE : rejects
  PRODUCTS ||--o{ ANALYSES : derives
  JOBS }o--|| PRODUCTS : recalculates
  USERS ||--o{ SESSIONS : authenticates
  USERS ||--o{ WATCHLISTS : owns
  PRODUCTS ||--o{ WATCHLISTS : tracked
  USERS ||--o{ NOTIFICATIONS : receives
  OBSERVATIONS ||--o{ NOTIFICATIONS : supports
  SCHEDULES ||--o{ JOBS : queues
```

Forecasts, event-memory matches, quality results and audit findings are embedded in versioned analysis documents rather than duplicated across separate collections.

## Ingestion sequence

```mermaid
sequenceDiagram
  participant A as Admin UI
  participant API as FastAPI
  participant DB as MongoDB
  participant W as Worker
  participant P as Bright Data
  A->>API: Submit allowlisted URLs with session + CSRF
  API->>DB: Reserve cap and persist queued job
  API-->>A: Job ID
  W->>DB: Atomically claim lease
  W->>DB: Mark submitting
  W->>P: Trigger configured scraper
  P-->>W: Snapshot ID
  W->>DB: Persist snapshot ID
  W->>P: Poll status and download when ready
  W->>DB: Raw records, valid observations, quarantine
  W->>DB: Queue affected analyses
  W->>DB: Evaluate watches and write notifications
  A->>API: Read job progress
  API-->>A: Persistent status and counts
```

## Analysis sequence

```mermaid
sequenceDiagram
  participant U as Product UI
  participant API as API
  participant S as Shared analysis services
  participant DB as MongoDB
  U->>API: Product + cutoff + window + horizon
  API->>DB: Read immutable observations
  API->>S: Filter availability at cutoff and comparable scope
  S->>S: Assess coverage, compute or abstain
  S-->>API: Quantities, evidence keys, uncertainty, versions
  API->>DB: Persist derived analysis
  API-->>U: Mode-labeled response envelope
  U->>API: Inspect evidence key
  API->>DB: Observation and raw record
  API-->>U: Source evidence
```
