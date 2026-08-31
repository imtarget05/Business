"""Golden dataset for RAG evaluation.

Each entry has:
- question: the query
- expected_chunk_ids: set of chunk IDs that should be retrieved (relevant documents)
- description: what the question is about
"""

GOLDEN_DATASET = [
    {
        "id": "q1",
        "question": "What is the capital of Vietnam?",
        "expected_chunk_ids": {"vietnam_geography"},
        "description": "Basic geography question about Vietnam",
    },
    {
        "id": "q2",
        "question": "How does hybrid retrieval combine FTS and vector search?",
        "expected_chunk_ids": {"hybrid_retrieval"},
        "description": "Technical question about retrieval architecture",
    },
    {
        "id": "q3",
        "question": "What is the refund policy timeframe?",
        "expected_chunk_ids": {"business_ops_refunds"},
        "description": "Business operations question about refunds",
    },
    {
        "id": "q4",
        "question": "How do embeddings enable semantic search?",
        "expected_chunk_ids": {"embeddings_guide"},
        "description": "AI/ML question about vector embeddings",
    },
    {
        "id": "q5",
        "question": "What agents exist in the system architecture?",
        "expected_chunk_ids": {"agent_architecture"},
        "description": "Technical question about agent design",
    },
    {
        "id": "q6",
        "question": "How long does shipping take for domestic orders?",
        "expected_chunk_ids": {"business_ops_shipping"},
        "description": "Business operations question about shipping",
    },
    {
        "id": "q7",
        "question": "What is RAG and why is it useful?",
        "expected_chunk_ids": {"rag_overview"},
        "description": "AI/ML question about Retrieval-Augmented Generation",
    },
    {
        "id": "q8",
        "question": "What Vietnamese cuisine is known in Da Nang?",
        "expected_chunk_ids": {"vietnam_culture"},
        "description": "Cultural question about Vietnamese food",
    },
    {
        "id": "q9",
        "question": "How does the knowledge base store document chunks?",
        "expected_chunk_ids": {"kb_implementation"},
        "description": "Technical question about KB storage",
    },
    {
        "id": "q10",
        "question": "What is the warranty coverage period?",
        "expected_chunk_ids": {"business_ops_warranty"},
        "description": "Business operations question about warranty",
    },
    {
        "id": "q11",
        "question": "How does cosine similarity measure vector relevance?",
        "expected_chunk_ids": {"vector_search_basics"},
        "description": "AI/ML question about similarity metrics",
    },
    {
        "id": "q12",
        "question": "What is the population of Ho Chi Minh City?",
        "expected_chunk_ids": {"vietnam_geography"},
        "description": "Geography question about Vietnamese cities",
    },
]

EVAL_DOCUMENTS = [
    {
        "doc_id": "vietnam_geography",
        "title": "Vietnam Geography",
        "content": (
            "Vietnam is a country in Southeast Asia. Its capital is Hanoi, "
            "located in the northern region. Ho Chi Minh City, formerly Saigon, "
            "is the largest city with a population of over 9 million people. "
            "Vietnam has a diverse landscape with mountains, rivers, and coastal areas."
        ),
    },
    {
        "doc_id": "hybrid_retrieval",
        "title": "Hybrid Retrieval Architecture",
        "content": (
            "Hybrid retrieval combines full-text search (FTS) and vector search "
            "using Reciprocal Rank Fusion (RRF). FTS uses inverted indexes to match "
            "keywords precisely. Vector search uses embeddings for semantic matching. "
            "RRF merges results by summing weighted reciprocal ranks, avoiding score "
            "normalization issues. The hybrid approach benefits from both lexical "
            "precision and semantic recall."
        ),
    },
    {
        "doc_id": "business_ops_refunds",
        "title": "Refund Policy",
        "content": (
            "Our refund policy allows customers to request refunds within 14 days "
            "of purchase. Refunds are processed to the original payment method. "
            "Items must be returned in original condition. Digital products are "
            "non-refundable after download."
        ),
    },
    {
        "doc_id": "embeddings_guide",
        "title": "Embeddings and Semantic Search",
        "content": (
            "Embeddings convert text into high-dimensional vectors that capture "
            "semantic meaning. Similar texts produce similar embedding vectors. "
            "The MockEmbeddingProvider generates deterministic hash-based vectors "
            "for testing. Production systems use models like bge-m3 or OpenAI "
            "embeddings for better semantic quality."
        ),
    },
    {
        "doc_id": "agent_architecture",
        "title": "Agent System Architecture",
        "content": (
            "The system uses multiple specialized agents: KnowledgeAgent for Q&A, "
            "SalesAgent for customer interactions, SupplyChainAgent for logistics, "
            "and AdvisoryAgent for business insights. Agents communicate through "
            "a shared orchestrator using LangGraph for stateful workflows."
        ),
    },
    {
        "doc_id": "business_ops_shipping",
        "title": "Shipping Information",
        "content": (
            "Shipping takes 3 to 5 business days for domestic orders within the US. "
            "International shipping takes 7 to 14 business days depending on the "
            "destination. Tracking numbers are sent via email once the order ships. "
            "Express shipping options are available at checkout."
        ),
    },
    {
        "doc_id": "rag_overview",
        "title": "RAG Overview",
        "content": (
            "Retrieval-Augmented Generation (RAG) combines information retrieval "
            "with language model generation. RAG systems first retrieve relevant "
            "documents from a knowledge base, then pass them as context to the LLM. "
            "This approach reduces hallucination and enables up-to-date responses "
            "without retraining the model."
        ),
    },
    {
        "doc_id": "vietnam_culture",
        "title": "Vietnamese Culture and Cuisine",
        "content": (
            "Vietnamese cuisine varies by region. Da Nang is known for mi quang, "
            "a noodle dish with shrimp and pork. Hanoi is famous for pho bo, "
            "beef noodle soup. Ho Chi Minh City offers banh mi sandwiches and "
            "com tam broken rice. Vietnamese food emphasizes fresh herbs and "
            "balanced flavors."
        ),
    },
    {
        "doc_id": "kb_implementation",
        "title": "Knowledge Base Implementation",
        "content": (
            "The KnowledgeBase class stores document chunks in a relational database. "
            "Each chunk has an id, doc_id, source_path, title, chunk_index, content, "
            "and optional embedding vector. The schema supports both PostgreSQL with "
            "pgvector and SQLite for local development and testing."
        ),
    },
    {
        "doc_id": "business_ops_warranty",
        "title": "Warranty Policy",
        "content": (
            "All products include a 12-month warranty covering manufacturing defects. "
            "Warranty claims require the original proof of purchase. The warranty "
            "does not cover damage from misuse, accidents, or normal wear and tear. "
            "Extended warranty options are available for purchase."
        ),
    },
    {
        "doc_id": "vector_search_basics",
        "title": "Vector Search Fundamentals",
        "content": (
            "Vector search finds documents by comparing embedding vectors. Cosine "
            "similarity measures the angle between vectors, ranging from -1 to 1. "
            "Higher cosine similarity indicates greater semantic relevance. The "
            "KnowledgeBase uses cosine similarity for ranking vector search results."
        ),
    },
    {
        "doc_id": "technology_stack",
        "title": "Technology Stack",
        "content": (
            "The platform uses Python with FastAPI for the web framework, SQLAlchemy "
            "for database access, LangGraph for agent orchestration, and PostgreSQL "
            "with pgvector for vector storage. Async/await patterns are used throughout "
            "for non-blocking I/O operations."
        ),
    },
    {
        "doc_id": "fts_basics",
        "title": "Full-Text Search",
        "content": (
            "Full-text search uses inverted indexes to match keywords efficiently. "
            "The system tokenizes text into terms and scores documents by term "
            "frequency. PostgreSQL uses tsvector for native FTS. SQLite falls back "
            "to in-Python token-overlap scoring for compatibility."
        ),
    },
    {
        "doc_id": "business_ops_support",
        "title": "Customer Support",
        "content": (
            "Customer support is available 24/7 via email and live chat. First "
            "response time averages under 2 hours for paid plans. The support "
            "team handles billing, technical issues, and account management queries. "
            "Support agents use the knowledge base to find accurate answers quickly."
        ),
    },
    {
        "doc_id": "agent_routing",
        "title": "Agent Routing",
        "content": (
            "Incoming queries are routed to the appropriate agent based on domain "
            "classification. The router uses keyword matching and confidence scoring "
            "to determine the best agent. Capability routing maps specific actions "
            "to agents that can handle them, ensuring efficient request handling."
        ),
    },
    {
        "doc_id": "vietnam_history",
        "title": "Vietnam History",
        "content": (
            "Vietnam has a rich history spanning thousands of years. The country "
            "gained independence from French colonial rule in 1954. The Vietnam "
            "War ended in 1975 with reunification. Today Vietnam is a rapidly "
            "developing economy with a young, tech-savvy population."
        ),
    },
    {
        "doc_id": "business_model",
        "title": "Business Model",
        "content": (
            "The platform operates on a SaaS subscription model with tiered pricing. "
            "The Business plan costs $29 per user per month, billed annually. "
            "Enterprise pricing is customized based on volume and feature requirements. "
            "Annual subscriptions include a 20% discount compared to monthly billing."
        ),
    },
]
