from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_user
from app.models.user import User
from app.schemas.ask import AskRequest, AskResponse, SourceReference
from app.services.code_retriever import retrieve_code_context, RepositoryNotFound, UnauthorizedRepositoryAccess, RetrievedContext
from app.services.ai_provider import generate_structured
from app.core.ai_errors import AIUnavailableError, AIQuotaExceededError, sanitize_ai_error
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repositories", tags=["Ask"])

def build_ask_prompt(question: str, context: list[RetrievedContext]) -> str:
    if not context:
        return f"""You are an expert static code analysis assistant.
The user asked: "{question}"

No relevant code context could be found in the repository for this question.

INSTRUCTIONS:
Return exactly this message in the 'answer' field: "I couldn't find enough relevant code in this repository to answer that confidently."
Return an empty array for 'sources'.
Do not invent files, functions, or APIs.
"""

    context_blocks = []
    for ctx in context:
        context_blocks.append(f"--- FILE: {ctx.file_path} (Lines {ctx.start_line}-{ctx.end_line}) ---\n{ctx.content}\n")
    
    context_str = "\n".join(context_blocks)

    return f"""You are an expert static code analysis assistant.
You are tasked with answering a question based ONLY on the provided repository source code context.

USER QUESTION:
{question}

REPOSITORY CONTEXT:
{context_str}

INSTRUCTIONS:
- Answer the user's question using ONLY the supplied repository context.
- Explicitly distinguish between confirmed repository facts (what is present in the code), uncertainty (what might be true but isn't fully clear), and unsupported assumptions (what is missing from the context).
- Keep the answer technically useful and concise.
- If the supplied context is completely insufficient to form an answer, explicitly state: "I couldn't find enough relevant code in this repository to answer that confidently."
- Do not invent files, functions, classes, APIs, or behavior not supported by the context.
- Do not pretend to have analyzed files that were not retrieved.
- Cite relevant source files by returning them in the 'sources' array of your JSON response.

Return your answer in valid JSON matching this schema:
{{
  "answer": "<Your concise answer>",
  "sources": [
    {{
      "file_path": "<Path to file>",
      "start_line": <Start line number>,
      "end_line": <End line number>
    }}
  ]
}}
"""

def process_sources(sources: list[SourceReference], context: list[RetrievedContext]) -> list[SourceReference]:
    valid_files = {}
    for ctx in context:
        if ctx.file_path not in valid_files:
            valid_files[ctx.file_path] = ctx.end_line
        else:
            valid_files[ctx.file_path] = max(valid_files[ctx.file_path], ctx.end_line)
            
    processed = []
    seen = set()
    
    for src in sources:
        if src.file_path not in valid_files:
            continue
            
        max_allowed_line = valid_files[src.file_path]
        
        # Clamp lines
        start_line = max(1, src.start_line)
        end_line = min(max_allowed_line, src.end_line)
        
        if start_line > end_line:
            start_line = max(1, min(src.start_line, max_allowed_line))
            end_line = max_allowed_line
            
        key = (src.file_path, start_line, end_line)
        if key not in seen:
            seen.add(key)
            processed.append(SourceReference(
                file_path=src.file_path,
                start_line=start_line,
                end_line=end_line
            ))
            
    return processed[:5]

@router.post("/{repository_id}/ask", response_model=AskResponse)
def ask_repository(
    request: AskRequest,
    repository_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question cannot be empty or whitespace")
    
    try:
        context = retrieve_code_context(db, repository_id, current_user.id, question)
    except RepositoryNotFound:
        raise HTTPException(status_code=404, detail="Repository not found")
    except UnauthorizedRepositoryAccess:
        raise HTTPException(status_code=403, detail="Not authorized to access this repository")

    prompt = build_ask_prompt(question, context)
    
    try:
        response, provider = generate_structured(
            prompt=prompt,
            schema=AskResponse,
            temperature=0.2
        )
        
        # Validate and sanitize response sources
        response.sources = process_sources(response.sources, context)
        
        # Enforce insufficient context fallback natively if AI tries to output something but has 0 context
        if not context and response.answer != "I couldn't find enough relevant code in this repository to answer that confidently.":
            response.answer = "I couldn't find enough relevant code in this repository to answer that confidently."
            
        return response
    except (AIUnavailableError, AIQuotaExceededError) as e:
        logger.error(f"AI error in ask: {e}")
        raise HTTPException(status_code=503, detail=sanitize_ai_error(e))
    except Exception as e:
        logger.error(f"Unexpected error in ask: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred during generation.")
