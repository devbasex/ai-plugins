# 図表作成ガイド

## mermaid 記法

### フローチャート

```mermaid
graph TD
    A[開始] --> B{条件判定}
    B -->|Yes| C[処理A]
    B -->|No| D[処理B]
    C --> E[終了]
    D --> E
```

### シーケンス図

```mermaid
sequenceDiagram
    User->>API: リクエスト
    API->>DB: クエリ
    DB-->>API: 結果
    API-->>User: レスポンス
```

### クラス図

```mermaid
classDiagram
    class User {
        +int id
        +string name
        +login()
        +logout()
    }
    class Order {
        +int id
        +float total
    }
    User "1" --> "*" Order
```

### ER図

```mermaid
erDiagram
    USER ||--o{ ORDER : places
    ORDER ||--|{ LINE_ITEM : contains
    PRODUCT ||--o{ LINE_ITEM : "ordered in"
```

## plantUML 記法

### コンポーネント図

```plantuml
@startuml
package "Frontend" {
    [React App]
}
package "Backend" {
    [API Server]
    [Database]
}
[React App] --> [API Server]
[API Server] --> [Database]
@enduml
```

### アクティビティ図

```plantuml
@startuml
start
:ユーザー入力;
if (有効?) then (yes)
    :処理実行;
else (no)
    :エラー表示;
endif
stop
@enduml
```

## ASCII 許可例（ツリーのみ）

ディレクトリ構造はASCIIで表現可能:

```
project/
├── src/
│   ├── components/
│   └── utils/
├── tests/
└── docs/
```

## よくある間違い

### 避けるべき: ASCII ARTで図を描く

```
    ┌─────────┐
    │  User   │
    └────┬────┘
         │
    ┌────▼────┐
    │   API   │
    └─────────┘
```

上記のような図は **mermaid** で描いてください:

```mermaid
graph TD
    User --> API
```

### 避けるべき: 順序prefixなしで分割

```
docs/
├── introduction.md  ← NG: prefixがない
├── setup.md
└── usage.md
```

正しい方法:

```
docs/
├── 01-introduction.md  ← OK
├── 02-setup.md
└── 03-usage.md
```

## ベストプラクティス

| DO | DON'T |
|----|-------|
| mermaid/plantUMLで図を描く | ASCII ARTで図を描く |
| 300行以内に収める | 1000行超の巨大ファイル |
| 順序prefixで分割 | prefixなしで分割 |
| 2桁パディング（01-, 02-） | 1桁（1-, 2-） |
