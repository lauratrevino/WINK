"""
Retrieval: split documents into overlapping chunks, and rank those chunks
by relevance to a specific question.

Why this exists: the old approach sent the model a fixed share of *every*
uploaded document's raw text, divided evenly regardless of size — which
worked fine for one or two short documents, but as a student uploads more
material, each document's share shrinks and the *tail* of a document
(whatever didn't fit) is silently dropped, with no regard for whether that
tail actually mattered to the question being asked. For a tool where
answer accuracy is the first priority, that's backwards: it should get
*more* precise as more material piles up, not blindly more truncated.

The fix used throughout this module: when everything comfortably fits
under the budget, include it ALL, exactly as before — no chunking, no
ranking, no information lost, for the common case. Chunking and ranking
only kick in once there's genuinely too much material to fit, and at that
point the goal is to find the passages that actually answer THIS question,
not truncate every document by the same blind percentage regardless of
relevance.

Two ranking backends:
- TF-IDF (scikit-learn), the original default — needs no model download,
  runs fully offline, verified against this app's real Postgres instance
  and its own real uploaded syllabi. Ranks by literal word overlap, so
  it's very good at "when is the midterm" and weaker at pure paraphrase
  ("when do I get graded on the big test" against a syllabus that only
  ever says "examination").
- Neural embeddings via Voyage AI (Anthropic's recommended embeddings
  partner), used automatically whenever VOYAGE_API_KEY is set and every
  candidate chunk already has a precomputed embedding stored (see
  store_document_chunks() in services/documents.py). Understands
  "textbook" and "required reading" are related without being told so
  explicitly — the actual fix for TF-IDF's paraphrase gap. NOTE: the
  Voyage API itself was never reachable from the sandbox this was built
  in (same network restriction that blocked huggingface.co earlier), so
  this was built and tested against a fake client that mimics Voyage's
  documented response shape — the real API call has not been verified
  end-to-end. Test this for real as one of the first things you do once
  it's deployed somewhere with normal internet access.
"""
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .. import config
from ..errors import log_error
from ..extensions import voyage_client

# TF-IDF only matches literal words, so a question phrased differently from
# the document ("textbook" vs. a syllabus that only ever says "Required
# Text") can miss even when the answer is right there — confirmed directly
# against a real uploaded syllabus during development (see README.md's
# "Upgrading retrieval accuracy" section for the actual before/after test).
# This is a cheap, deterministic partial fix: expand the query with common
# academic-vocabulary equivalents before ranking, so a synonym in the
# question still overlaps with the document's actual wording. It's a
# curated list, not a general solution — it helps exactly the common
# paraphrases below and nothing else. A neural embedding model would
# understand synonyms it was never told about; this only knows the ones
# listed here.
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
    """Appends known synonyms for any academic terms found in the
    question, so the TF-IDF match below isn't purely literal. Returns the
    original question unchanged if none of the known terms appear —
    this never removes or reweights anything, only adds extra words for
    the vectorizer to potentially match on."""
    lower = question.lower()
    extra = []
    for term, synonyms in _ACADEMIC_SYNONYMS.items():
        if term in lower:
            extra.extend(synonyms)
    return question + " " + " ".join(extra) if extra else question


def chunk_text(text, header=""):
    """Splits text into overlapping chunks of roughly
    RETRIEVAL_CHUNK_CHARS characters, breaking on paragraph boundaries
    where possible so a chunk doesn't cut a sentence in half more than
    necessary. Each chunk is prefixed with `header` (e.g. the document
    name and course) so a retrieved chunk is self-describing when it's
    later injected into the prompt on its own, without its surrounding
    document for context.

    Overlap (RETRIEVAL_CHUNK_OVERLAP_CHARS) means consecutive chunks share
    a bit of text at the boundary, so a fact that happens to fall right at
    a chunk break (e.g. "the midterm is on|March 3rd") still appears whole
    in at least one chunk instead of being split across two and found in
    neither.
    """
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
            # Start the next chunk with the tail of the previous one, so
            # nothing at the boundary is lost.
            current = current[-overlap:] + "\n\n" + para if overlap else para
        else:
            current = (current + "\n\n" + para) if current else para
        # A single paragraph longer than the whole chunk size (a dense
        # block of text with no blank-line breaks) still needs to be split
        # on its own, or it would produce one giant chunk.
        while len(current) > size * 1.5:
            chunks.append(current[:size])
            current = current[size - overlap:]

    if current:
        chunks.append(current)

    if header:
        return [f"{header}\n{c}" for c in chunks]
    return chunks


def _rank_tfidf(question, chunks):
    """Ranks chunks by TF-IDF cosine similarity to the question. Fits a
    fresh vectorizer over exactly this question + these chunks every call
    — deliberately not a precomputed/stored index, since TF-IDF's
    vocabulary depends on the current document set and there's no
    meaningful way to keep a stored vector comparable as new documents are
    added. At the scale this runs at (one student's own chunks, or one
    university's reference chunks — dozens to a few hundred, not
    millions), refitting per question is fast and there's nothing to
    gain from caching it."""
    try:
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform([_expand_query(question)] + chunks)
        sims = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
        return sorted(range(len(chunks)), key=lambda i: sims[i], reverse=True)
    except ValueError:
        # Can happen if every chunk + the question are only stop words /
        # punctuation (TfidfVectorizer ends up with an empty vocabulary) —
        # fall back to original order rather than erroring the whole request.
        return list(range(len(chunks)))


def embed_texts(texts, input_type):
    """Embeds a batch of texts via Voyage AI. `input_type` should be
    "document" when embedding chunks to store, or "query" when embedding
    a student's question — Voyage's models are trained asymmetrically, so
    using the right one for each side measurably improves match quality
    over treating both the same way. Returns a list of embedding vectors
    (each a plain list of floats), or None if the client isn't configured
    or the call fails for any reason — callers must treat None as "neural
    embeddings aren't available right now", not a fatal error; every
    caller in this module already does."""
    if not voyage_client or not texts:
        return None
    try:
        result = voyage_client.embed(texts, model=config.EMBEDDING_MODEL, input_type=input_type)
        return result.embeddings
    except Exception as e:
        log_error("services.retrieval.embed_texts", e)
        return None


def _rank_neural(question, chunks, chunk_embeddings):
    """Ranks chunks by real semantic similarity using PRECOMPUTED chunk
    embeddings (see store_document_chunks() in services/documents.py,
    which computes and stores these once, at upload time) — this function
    only ever makes one new embedding call, for the question itself, not
    one per chunk, every single time a student asks something. Voyage
    embeddings are unit-normalized, so a plain dot product already IS
    cosine similarity — no separate normalization step needed.
    Raises NotImplementedError (caught by rank_chunks() below) if neural
    ranking isn't usable right now: no client configured, or any chunk in
    this set is missing its precomputed embedding (e.g. it was uploaded
    before this feature existed, or embedding failed at upload time) —
    in either case the honest answer is "fall back to TF-IDF for this
    request" rather than silently mixing ranked and unranked chunks."""
    if not voyage_client or chunk_embeddings is None or any(e is None for e in chunk_embeddings):
        raise NotImplementedError
    query_embeddings = embed_texts([question], input_type="query")
    if not query_embeddings:
        raise NotImplementedError
    query_vec = query_embeddings[0]
    sims = [sum(x * y for x, y in zip(query_vec, vec)) for vec in chunk_embeddings]
    return sorted(range(len(chunks)), key=lambda i: sims[i], reverse=True)


def rank_chunks(question, chunks, top_n, chunk_embeddings=None):
    """Returns the top_n chunks (as a list of strings, in ranked-relevance
    order) most relevant to `question`. Uses precomputed neural embeddings
    (see `chunk_embeddings` — a list parallel to `chunks`, each entry
    either an embedding vector or None) if all of them are present and a
    Voyage client is configured; otherwise TF-IDF."""
    if not chunks:
        return []
    if len(chunks) <= top_n:
        return chunks
    try:
        order = _rank_neural(question, chunks, chunk_embeddings)
    except NotImplementedError:
        order = _rank_tfidf(question, chunks)
    return [chunks[i] for i in order[:top_n]]
