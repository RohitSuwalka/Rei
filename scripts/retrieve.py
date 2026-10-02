import chromadb
from sentence_transformers import SentenceTransformer

# Connect to ChromaDB
client = chromadb.PersistentClient(path="./chroma_db")

collection = client.get_collection("study_notes")

# Load embedding model
model = SentenceTransformer("all-MiniLM-L6-v2")

# Test question
query = input("Ask a question: ")

# Convert question to embedding
query_embedding = model.encode(query)

# Search
results = collection.query(
    query_embeddings=[query_embedding.tolist()],
    n_results=3
)

print("\nTop Results")
print("=" * 50)

documents = results["documents"]
if not documents or not documents[0]:
    print("No matching documents found.")
    raise SystemExit(0)

for i, doc in enumerate(documents[0]):
    print(f"\nResult {i+1}")
    print("-" * 30)
    print(doc[:1000])