# Context Compression: Making AI Long Conversations More Efficient

## 1. Concept Explanation

### 1.1 The Concept of Context

In AI dialogue systems, **context** refers to all historical information generated during a conversation, including user questions, AI answers, tool call results, etc. This information together forms the basis for the AI to understand the current dialogue state and provide accurate responses.

### 1.2 Context Window Limitations

Although context is crucial for AI to understand conversations, all AI models have **context window limitations** - the maximum amount of information (usually measured in tokens) that a model can process in a single conversation. When conversations exceed this limit, the AI will exhibit the following problems:

- Significantly slower response times
- Forgetting early key information
- Decreased answer accuracy
- Excessive system resource consumption

### 1.3 The Concept of Context Compression

**Context compression** is an intelligent optimization technology that analyzes, filters, and streamlines historical dialogue content to compress context information within the model's processable range while maintaining conversation coherence.

Its core idea can be analogized to reading a long novel - we don't need to remember every detail, only key plot points and character relationships, to understand the story's development.

### 1.4 Implementation Principles of Context Compression

JiuwenSwarm's context compression system uses the following core technologies:

1. **Intelligent Recognition**: The system automatically identifies key information and redundant content in conversations
   - Distinguishes important messages from secondary messages through semantic analysis
   - Identifies duplicate information and low-value content

2. **Selective Compression**: Adopts different processing strategies for different types of content
   - Preserves user questions and AI core answers
   - Summarizes lengthy tool return results
   - Merges duplicate information

3. **Index Marking**: Inserts special markers in compressed context
   - Uses `[[OFFLOAD:...]]` format index markers for offloaded large tool messages
   - Preserves retrieval capabilities for offloaded tool messages

4. **Dynamic Regulation**: Automatically adjusts compression strategies based on dialogue state
   - Triggers compression when message count or token count exceeds thresholds
   - Can be configured to keep the most recent messages uncompressed

## 2. Basic Usage

### 2.1 Enable Context Compression

Context compression is disabled by default. You can enable it in the following ways:

**Method 1: Enable via Configuration File**

Edit the `config.yaml` configuration file, find the `context_engine_config` section:

```yaml
    enabled: true         # Set this to true to enable context compression
```

**Method 2: Enable via Frontend Interface**

In the JiuwenSwarm client, navigate to:

![Context Compression Settings](../assets/images/上下文压缩设置.png)

- Click "Configuration" in the left menu bar
- Select "Other Configurations"
- Find "Context Compression"
- Toggle the switch to enable it

### 2.2 View Context Compression Status

After enabling context compression, you can check the compression status as follows:

**Method 1: Check Conversation Status Panel**

In the bottom right corner of the conversation interface, the system displays the real-time usage of the context window:

![Context Compression Status Panel](../assets/images/上下文压缩_开启.png)

The status panel includes:
- Current Token count used
- Maximum Token count supported by the model
- Current usage percentage

**Method 2: View via Slash Command**

Enter the following command in the conversation input box to view detailed context compression information:

```bash
/context
```
![Context Status Panel](../assets/images/上下文状态.png)

## 3. Case Practice

### 3.1 Without Context Compression

**Scenario**: User has a long data analysis conversation with AI

**Process**:
1. User asks: "Help me analyze this month's sales data"
2. AI requests data
3. User uploads a 5000-token sales data table
4. AI analyzes and provides preliminary conclusions
5. User continues: "Help me compare with last month's data"
6. AI requests last month's data
7. User uploads a 4500-token sales data table for last month
8. AI analyzes and provides comparison results
9. User continues: "What about next year's sales forecast?"

**Results**:
- Total context tokens exceed 10,000
- AI response time significantly slows (from 2 seconds to 10 seconds)
- AI begins to forget some key indicators mentioned earlier
- System resource utilization increases significantly

### 3.2 With Context Compression

**Same Scenario**: User has a long data analysis conversation with AI

**Process**:
1. User asks: "Help me analyze this month's sales data"
2. AI requests data
3. User uploads a 5000-token sales data table
4. AI analyzes and provides preliminary conclusions
5. User continues: "Help me compare with last month's data"
6. AI requests last month's data
7. User uploads a 4500-token sales data table for last month
8. System detects token count approaching threshold and automatically triggers compression
9. System summarizes and compresses sales data tables, retaining only key statistical information
10. AI analyzes and provides comparison results
11. User continues: "What about next year's sales forecast?"

**Results**:
- Total context tokens remain around 3,000
- AI response time is stable (2-3 seconds)
- AI can completely remember all key indicators and analysis results
- System resource utilization remains at a reasonable level

### 3.3 Travel Planning Example

**Scenario**: User asks AI to plan a 3-day trip to Beijing (single request)

**Conversation Process (Without Compression)**:

```
User: Help me plan a 3-day trip to Beijing next week
Assistant: Sure, I'll plan a detailed 3-day itinerary for you.
Tool: Querying weather and attraction information for Beijing...
Tool Response: Returns detailed weather data and attraction recommendations
Assistant: Based on your requirements, here's your travel plan:

[3-Day Beijing Tour Itinerary]

Day 1: Tiananmen Square → Forbidden City → Jingshan Park
- Morning: Watch the flag-raising at Tiananmen Square
- Noon: Explore the Forbidden City
- Afternoon: Overlook the Forbidden City from Jingshan Park

Day 2: Badaling Great Wall → Ming Tombs
- Morning: Badaling Great Wall
- Afternoon: Ming Tombs

Day 3: Summer Palace → Yuanmingyuan → Nanluoguxiang
- Morning: Summer Palace
- Noon: Yuanmingyuan
- Afternoon: Experience old Beijing culture at Nanluoguxiang

Food Recommendations: Peking Duck, Zhajiangmian, Hot Pot

Important Reminder: Please book Forbidden City tickets in advance
```

**Explanation**:
- The AI response contains rich travel information
- Status panel at bottom right shows: Context Window 41.3K/289.0K (20.6%)
- Token consumption mainly comes from: AI's detailed response + Tool call return data

**Results**:
- Total context tokens: 41.3K, occupying 20.6% of the model window
- Risk: If user continues asking for details or expanding the plan, context will continue growing
- Potential issue: May approach the model's context window limit as conversation progresses

![Context Compression Disabled](../assets/images/上下文压缩_未开启.png)

**Conversation Process (With Compression)**:

```
User: Help me plan a 3-day trip to Beijing next week
Assistant: Sure, I'll plan a detailed 3-day itinerary for you.
Tool: Querying weather and attraction information for Beijing...
Tool Response: Returns detailed weather data and attraction recommendations
Assistant: Based on your requirements, here's your travel plan:

[3-Day Beijing Tour Itinerary]

Day 1: Tiananmen Square → Forbidden City → Jingshan Park
...(System automatically compressed detailed itinerary content)

[[OFFLOAD: Travel plan details compressed, core information retained]]
```

**Explanation**:
- AI completed the full travel planning task
- Status panel at bottom right shows: Context Window 40.7K/289.0K (28.3%)

**Results**:
- Total context tokens remain at 40.7K, occupying 28.3% of the model window
- AI retains core information: Can handle subsequent requests
- System supports additional tasks: Such as creating skills
- Improved user experience: Enables continued multi-turn interactions and feature extensions

![Context Compression Enabled](../assets/images/上下文压缩_开启.png)

### 3.4 Effect Analysis

Based on the actual data from the examples:

1. **Context Token Usage**:
   - Without compression: 41.3K tokens
   - With compression: 40.7K tokens
   - The difference is small, indicating that compression mainly optimizes information structure rather than simply reducing token count

2. **System Performance**:
   - Both scenarios successfully generated travel plans
   - Compressed scenario additionally supports skill creation and other extended features
   - Memory usage stable around 362MB

3. **Practical Value**:
   - Context compression optimizes information structure, enabling better handling of subsequent extension requests
   - Enhances system functionality while maintaining core information integrity

### 3.5 Comparison Summary

| Metric | Without Context Compression | With Context Compression |
|--------|------------------------------|---------------------------|
| Response Speed | Gradually slows (10+ seconds) | Stable (2-3 seconds) |
| Information Integrity | Gradually lost | Key information fully retained |
| System Resource Usage | High (> 2GB memory) | Low (< 1GB memory) |
| Conversation Coherence | Gradually decreases | Remains consistent |
| Supported Conversation Length | Limited (about 20-30 rounds) | Unlimited (supports hundreds of rounds) |

## 4. Feature Interpretation

### 4.1 Dynamic Triggering Mechanism

Context compression is not a fixed preprocessing step, but an intelligent optimization dynamically triggered based on dialogue state:

- **Based on Context Occupancy Ratio**: Triggers when the context (system prompt + tool definitions + messages) reaches the `trigger_context_ratio` fraction of the window capacity
- **Based on Absolute Tokens**: When `trigger_token_threshold` is set, compression triggers once the context reaches that absolute token count (takes precedence over the ratio)
- **Recent-Message Retention**: Each compressor keeps recent raw messages via `keep_recent_messages` for context continuity
- **Configurable**: Users can adjust trigger thresholds as needed (see Section 6)

### 4.2 Intelligent Recognition and Compression

The system uses advanced natural language processing technology to:

- **Distinguish Message Types**: Automatically identifies user messages, AI answers, and tool results
- **Identify Importance**: Judges message importance based on semantic analysis
- **Select Compression Methods**: Adopts different compression strategies for different content types
  - Summarizes lengthy tool return results
  - Merges duplicate information
  - Filters unimportant content

### 4.3 Key Information Protection

Context compression does not lose important information; the system:

- **Preserves Recent Messages**: Retains a specified number of latest raw messages through `keep_recent_messages`
- **Preserves Complete Rounds**: Compressors align boundaries to tool calls, avoiding splitting an in-progress tool-call round
- **Identifies Key Content**: Automatically identifies and retains key information through semantic analysis

### 4.4 Retrieval Capability Preservation

> **Note**: Normally compressed content cannot be recalled. Only offloaded large tool message content can be retrieved through `[[OFFLOAD:...]]` index markers.

- **Index Marking**: Uses `[[OFFLOAD:...]]` format index markers for offloaded large tool messages
- **Association Preservation**: Offloaded information remains associated with the original context
- **Recoverability**: Offloaded tool message information can be recovered through indices when needed

### 4.5 Performance Optimization

Context compression can significantly improve system performance:

- **Reduces Memory Usage**: Reduces context data volume by 50%-80%
- **Improves Response Speed**: Reduces model inference time and improves user experience
- **Supports Longer Conversations**: Breaks through model context window limitations and supports long conversations
- **Reduces Costs**: Reduces token consumption and lowers usage costs

## 5. Application Scenarios

Context compression technology is suitable for various scenarios requiring long conversations:

- **Data Analysis**: Processing large data tables and analysis results
- **Project Management**: Cross-day project follow-up and discussions
- **Code Development**: Long-time code writing and debugging
- **Document Creation**: Collaborative writing of long documents
- **Customer Support**: Long-time resolution of complex issues

## 6. Advanced Configuration

JiuwenSwarm's context compression is driven by openjiuwen's **forked (refactored) processors**, comprising 4 primary processors plus 1 optional loop-compaction processor. Configuration is passed through the `xxx_config` dictionaries under `react.context_engine_config`; the fields below match what the code actually honours.

> ⚠️ Note: the legacy compressors used fields such as `tokens_threshold`, `compression_target_tokens`, `messages_to_keep`, and `first_pass_target_tokens`. These fields are **no longer effective** in the refactored version (they are silently ignored). Refer to this document and `config.yaml` for the effective fields.

### 6.1 Complete Configuration Example (Effective Fields)

```yaml
context_engine_config:
  # Global switches
  enabled: true                    # Enable context compression (core switch)
  enable_reload: true              # Enable reload
  enable_openrouter_model_context_window_tokens: true # Resolve context window size per model

  # 1. Message summary offloader - handles tool-call and other specific messages
  message_summary_offloader_config:
    add_message_threshold_ratio: 0.1   # Context occupancy ratio at which a new message is processed
    ttl_seconds: 300                   # Idle seconds for TTL processing
    ttl_context_occupancy_ratio: 0.5   # Context occupancy ratio above which TTL is eligible
    ttl_message_threshold_ratio: 0.05  # Per-message ratio threshold for one TTL batch

  # 2. Dialogue compressor - compresses the whole dialogue history
  dialogue_compressor_config:
    trigger_context_ratio: 0.8        # Trigger compression at this context occupancy ratio
    min_target_context_ratio: 0.1     # Minimum fraction floor of the compressed content
    trigger_token_threshold: 200000   # Optional: absolute token trigger (takes precedence over ratio)
    target_retention_ratio: 0.3       # Optional: keep ~30% after compression

  # 3. Current-round compressor - compresses the current dialogue round
  current_round_compressor_config:
    trigger_context_ratio: 0.8
    min_target_context_ratio: 0.1
    keep_recent_messages: 4           # Number of most-recent raw messages kept
    trigger_token_threshold: 200000
    target_retention_ratio: 0.3

  # 4. Round-level compressor - advanced multi-round compression
  round_level_compressor_config:
    trigger_context_ratio: 0.8
    min_target_context_ratio: 0.1
    keep_recent_messages: 4
    trigger_token_threshold: 200000
    target_retention_ratio: 0.3

  # 5. Reasoning/tool loop compaction (optional, disabled by default)
  reasoning_tool_loop_compact_config:
    enabled: false
```

### 6.2 Parameter Reference

#### Message Summary Offloader (`message_summary_offloader_config`)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `add_message_threshold_ratio` | Context occupancy ratio at which a newly added tool message is processed | 0.1 |
| `ttl_seconds` | Idle seconds between context-window requests before TTL processing | 300 |
| `ttl_context_occupancy_ratio` | Context occupancy ratio above which TTL processing is eligible | 0.5 |
| `ttl_message_threshold_ratio` | Per-message ratio threshold for one TTL batch | 0.05 |

#### Dialogue Compressor (`dialogue_compressor_config`)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `trigger_context_ratio` | Trigger ratio (used when `trigger_token_threshold` is unset) | 0.8 |
| `min_target_context_ratio` | Minimum fraction floor of the compressed content | 0.1 |
| `trigger_token_threshold` | Optional: absolute token trigger, takes precedence over `trigger_context_ratio` | unset |
| `target_retention_ratio` | Optional: target retention ratio (summary ≈ compressed content tokens × ratio) | unset |

#### Current-Round Compressor (`current_round_compressor_config`)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `trigger_context_ratio` | Trigger ratio (used when `trigger_token_threshold` is unset) | 0.8 |
| `min_target_context_ratio` | Minimum fraction floor of the compressed content | 0.1 |
| `keep_recent_messages` | Number of most-recent raw messages kept | 0 |
| `trigger_token_threshold` | Optional: absolute token trigger, takes precedence over `trigger_context_ratio` | unset |
| `target_retention_ratio` | Optional: target retention ratio | unset |

#### Round-Level Compressor (`round_level_compressor_config`)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `trigger_context_ratio` | Trigger ratio (used when `trigger_token_threshold` is unset) | 0.8 |
| `min_target_context_ratio` | Minimum fraction floor of the compressed content | 0.1 |
| `keep_recent_messages` | Number of most-recent raw messages kept | 4 |
| `trigger_token_threshold` | Optional: absolute token trigger, takes precedence over `trigger_context_ratio` | unset |
| `target_retention_ratio` | Optional: target retention ratio | unset |

#### Reasoning / Tool Loop Compaction (`reasoning_tool_loop_compact_config`)

| Parameter | Description | Default |
|-----------|-------------|---------|
| `enabled` | Whether loop compaction is enabled | false |
| `consecutive_threshold` | Consecutive identical reasoning + tool set rounds to trigger compaction | 3 |
| `tool_args_consecutive_threshold` | Consecutive identical tool set + args rounds to trigger compaction | 5 |
| `bailout_threshold` | Bail out after this many reasoning-rule compactions | 3 |
| `tool_args_bailout_threshold` | Bail out after this many tool-args-rule compactions | 2 |

### 6.3 Absolute-Token Trigger and Target Retention Ratio

The three compressors (`dialogue_compressor_config`, `current_round_compressor_config`, `round_level_compressor_config`) support two optional parameters:

- **`trigger_token_threshold`**: an absolute token trigger threshold. When set, compression only triggers once the context (system prompt + tool definitions + messages) reaches this many tokens, and it takes **precedence over** `trigger_context_ratio`. When omitted, compression falls back to the ratio-based trigger.
- **`target_retention_ratio`**: a target retention ratio. When set, the compression summary aims for approximately `ratio × tokens(compressed content)` (e.g. `0.3` keeps ~30%), implemented by appending a length instruction to the compression prompt and setting a `max_tokens` cap. When omitted, no output-size control is applied (default behaviour).

```yaml
context_engine_config:
  dialogue_compressor_config:
    trigger_context_ratio: 0.8        # fallback ratio
    trigger_token_threshold: 200000   # optional: compress only at 200k tokens
    target_retention_ratio: 0.3       # optional: keep ~30% after compression
  current_round_compressor_config:
    trigger_token_threshold: 200000
    target_retention_ratio: 0.3
  round_level_compressor_config:
    trigger_token_threshold: 200000
    target_retention_ratio: 0.3
```

> Both parameters are optional and default to unset (`None`), so behaviour is identical to previous versions when they are omitted.

### 6.4 Configuration Notes

1. **Processors work independently**: each processor triggers on its own, with no interdependency.
2. **Trigger strategy**: by default, compression triggers by context occupancy ratio; to trigger by a fixed absolute token count, set `trigger_token_threshold` (e.g. `200000`).
3. **Required types**: `trigger_context_ratio` / `min_target_context_ratio` are decimals in `(0, 1)`; `target_retention_ratio`, when set, must also be a decimal in `(0, 1)`.
4. **Multi-level compression**: `round_level_compressor_config` and `current_round_compressor_config` keep recent raw messages via `keep_recent_messages`, balancing compression benefit with context continuity.

By configuring context compression parameters reasonably, users can obtain the best dialogue experience according to their usage scenarios and needs.