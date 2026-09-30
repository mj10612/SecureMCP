# SecureMCP Technical Architecture

SecureMCP operates as an intelligent local proxy implementing the Model Context Protocol (MCP). It intercepts text and code prompts before they are dispatched to remote AI endpoints, obscuring sensitive semantic tokens while preserving the grammatical and syntactic backbone.

---

## 1. High-Level System Architecture

```mermaid
flowchart TD
    subgraph Client ["Client Workstation (Claude Desktop / Cursor / Custom Agent)"]
        UserPrompt["Raw User Prompt / Code"] --> Tokenizer["Multilingual Tokenizer"]
        Tokenizer --> Classifier["Grammar & Syntax Classifier"]
        
        subgraph Vault ["Secure Session Vault (Local RAM)"]
            FwdMap["Forward Table: Original -> Surrogate"]
            RevMap["Reverse Table: Surrogate -> Original"]
        end
        
        Classifier -->|Content Tokens| Masker["Masking Engine"]
        Classifier -->|Function Words / Syntax| Skeleton["Grammar Preserver"]
        Masker <--> Vault
        
        Skeleton --> Assembled["Masked Prompt"]
    end
    
    subgraph Cloud ["Remote AI Provider (OpenAI / Anthropic Claude)"]
        Assembled -->|Encrypted HTTPS API| LLM["LLM Reasoning & Generation"]
        LLM --> AIResponse["AI Response (Contains Surrogates)"]
    end
    
    subgraph Restoration ["Local Restoration Pipeline"]
        AIResponse --> Unmasker["SecureMCP Unmasker"]
        Unmasker <--> RevMap
        Unmasker --> FinalOutput["Restored Confidential Output"]
    end
```

---

## 2. Linguistic Decomposition Pipeline

### English Syntax Separation
```mermaid
flowchart LR
    Token["Input Token"] --> CheckClosed{"Is Closed-Class Word?"}
    CheckClosed -->|Yes: the, in, with, is, and...| Preserve["Preserve As Grammar"]
    CheckClosed -->|No| CheckEntity{"Is Entity / Number / Secret?"}
    CheckEntity -->|Yes: Company, Email, $43M| GenEntity["Assign [ENT_n] / [NUM_n]"]
    CheckEntity -->|No: 일반 명사 / 동사| GenContent["Assign [NOUN_n] / [VERB_n]"]
```

### Korean Agglutinative Morphology Engine (교착어 형태소 분리)
In Korean, words (어절) bind substantive stems (체언) with grammatical particles (조사) and endings (어미):

$$\text{어절} = \text{체언 (실질 형태소)} + \text{조사 (형식 형태소)}$$

```mermaid
sequenceDiagram
    participant Text as "원문: 삼성전자가"
    participant Engine as "KoreanGrammarEngine"
    participant Vault as "Session Vault"
    participant Output as "LLM 프롬프트"

    Text->>Engine: Analyze '삼성전자가'
    Engine->>Engine: Longest-match josa extraction ('가')
    Engine->>Engine: Stem: '삼성전자' (Entity), Suffix: '가' (Subject Josa)
    Engine->>Vault: Check or create surrogate for '삼성전자'
    Vault-->>Engine: Returns '[ENT_1]'
    Engine->>Output: Concatenate surrogate + suffix -> '[ENT_1]가'
```

---

## 3. Code-Aware Obfuscation AST Engine

When processing source code (Python, TypeScript, SQL, Rust, Go, Java, C++):
- **Preserved Unchanged:**
  - Control keywords: `def`, `class`, `function`, `return`, `if`, `else`, `async`, `await`, `SELECT`, `WHERE`, `JOIN`
  - Built-in runtime symbols: `len`, `range`, `print`, `console.log`, `JSON.stringify`
  - Operators & syntax: `{}`, `()`, `[]`, `=>`, `+`, `-`, `*`, `;`, `:`, `.`
  - Indentation, newlines, and code structural geometry.
- **Obfuscated:**
  - Function / Method identifiers $\to$ `[ID_1]`
  - Variable / Parameter identifiers $\to$ `[ID_2]`
  - String literals $\to$ `"[LIT_1]"`
  - Numeric constants $\to$ `[NUM_1]`

---

## 4. Security & Privacy Guarantees

1. **Zero Cloud Disclosures:** No original tokens, mapping entries, or session metadata ever leave the local host.
2. **Volatile Memory Storage:** Mappings reside in Python process memory and are purged upon session termination or TTL expiration.
3. **No External Network Dependencies:** SecureMCP requires no internet access to perform tokenization, grammar parsing, or masking.
