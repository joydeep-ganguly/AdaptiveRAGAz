import re
from rank_bm25 import BM25Plus


def extract_headings_with_context(text):
  
  """
  Extract markdown headings with one paragraph of content after them.
  
  Args:
      text: Document text content
  
  Returns:
      List of extracted heading + content chunks
  """

  chunks = []

  sections = text.split('\n\n')

  i = 0
  while i < len(sections):
    section = sections[i].strip()

    pattern = r"^#+\s+"
    if re.match(pattern, section):
      heading = section

      # get the next paragraph after the heading
      if i+1 < len(sections):
        next_content = sections[i+1].strip()

        chunk = f"{heading}\n\n{next_content}"
        i = i + 2
      else:
        chunk = heading
        i = i + 1
      
      chunks.append(chunk)

    else:
      i = i + 1
  
  return chunks

# ##### Ranking documents using BM25Plus

def rank_documents_by_keywords(docs, keywords, k=5):
  # 5 -> 20 retriever -> MMR 20 * 20 
  """
  Rank documents using BM25Plus on heading+content chunks.
  
  Args:
      docs: List of Document objects to rank
      keywords: List of keywords to rank by
      k: Number of top documents to return
  
  Returns:
      List of top-k Document objects sorted by BM25 score
  """

  if not docs or not keywords:
    print('Either no docs or keywords found!')
    return docs
  
  query_tokens = ' '.join(keywords).lower().split(' ')

  # extract chunks from each document
  doc_chunks = []
  for doc in docs:
    chunks = extract_headings_with_context(doc.page_content)

    combined = ' '.join(chunks) if chunks else doc.page_content

    doc_chunks.append(combined.lower().split(' '))

  # Rank using BM23Plus
  bm25 = BM25Plus(doc_chunks)
  doc_scores = bm25.get_scores(query_tokens)

  ranked_indices = sorted(range(len(doc_scores)), key=lambda i: doc_scores[i], reverse=True)

  for rank, idx in enumerate(ranked_indices[:k],1):
    print(f"   [{rank}] Doc {idx}: score={doc_scores[idx]:.4f}")

  return [docs[i] for i in ranked_indices[:k]]
