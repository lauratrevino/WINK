import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .. import config
from ..errors import log_error
from ..extensions import voyage_client

_ACADEMIC_SYNONYMS = {
    "textbook": ["required text", "course material", "reading"],
    "midterm": ["exam", "examination", "test"],
    "final": ["final exam", "final examination"],
    "deadline": ["due date", "due"],
    "due": ["deadline", "due date"],
    "grade": ["grading", "score", "points"],
    "grading": ["grade", "points", "rubric"],
    "syllabus": ["course outline", "course schedule"],
    "professor": ["instructor", "faculty"],
    "instructor": ["professor", "faculty"],
    "office hours": ["availability", "meeting times"],
    "attendance": ["absence", "absences"],
    "late": ["late work", "late policy", "extension"],
    "extra credit": ["bonus", "bonus points"],
    "quiz": ["quizzes", "assessment"],
    "assignment": ["homework", "project", "coursework"],
}


def _expand_query(question):
    lower = question.lower()
    extra = []
    for term, synonyms in _ACADEMIC_SYNONYMS.items():
        if term in lower:
            extra.extend(synonyms)
    return question + " " + " ".join(extra) if extra else question


def chunk_text(text, header=""):
    text = (text or "").strip()
    if not text:
        return []

    size = config.RETRIEVAL_CHUNK_CHARS
    overlap = config.RETRIEVAL_CHUNK_OVERLAP_CHARS
    paragraphs = re.split(r"\n\s*\n", text)

    chunks = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current and len(current) + len(para) + 2 > size:
            chunks.append(current)
            current = current[-overlap:] + "\n\n" + para if overlap else para
        else:
            current = (current + "\n\n" + para) if current else para
        while len(current) > size * 1.5:
            chunks.append(current[:size])
            current = current[size - overlap:]

    if current:
        chunks.append(current)

    if header:
        return [f"{header}\n{c}" for c in chunks]
    return chunks


def _rank_tfidf(question, chunks):
    try:
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform([_expand_query(question)] + chunks)
        sims = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
        return sorted(range(len(chunks)), key=lambda i: sims[i], reverse=True)
    except ValueError:
        return list(range(len(chunks)))


def embed_texts(texts, input_type):
    if not voyage_client or not texts:
        return None
    try:
        result = voyage_client.embed(texts, model=config.EMBEDDING_MODEL, input_type=input_type)
        return result.embeddings
    except Exception as e:
        log_error("services.retrieval.embed_texts", e)
        return None


def _rank_neural(question, chunks, chunk_embeddings):
    if not voyage_client or chunk_embeddings is None or any(e is None for e in chunk_embeddings):
        raise NotImplementedError
    query_embeddings = embed_texts([question], input_type="query")
    if not query_embeddings:
        raise NotImplementedError
    query_vec = query_embeddings[0]
    sims = [sum(x * y for x, y in zip(query_vec, vec)) for vec in chunk_embeddings]
    return sorted(range(len(chunks)), key=lambda i: sims[i], reverse=True)


def rank_chunks(question, chunks, top_n, chunk_embeddings=None):
    if not chunks:
        return []
    if len(chunks) <= top_n:
        return chunks
    try:
        order = _rank_neural(question, chunks, chunk_embeddings)
    except NotImplementedError:
        order = _rank_tfidf(question, chunks)
    return [chunks[i] for i in order[:top_n]]
