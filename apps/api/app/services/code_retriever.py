import re
from typing import List
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.models.user import User

@dataclass
class RetrievedContext:
    file_path: str
    content: str
    start_line: int
    end_line: int
    relevance_score: float

class UnauthorizedRepositoryAccess(Exception):
    pass
class RepositoryNotFound(Exception):
    pass

MAX_FILES = 5
MAX_LINES_PER_FILE = 200
MAX_TOTAL_LINES = 1000

def _extract_keywords(question: str) -> List[str]:
    stopwords = {"how", "do", "i", "what", "is", "the", "a", "an", "where", "can", "find", "code", "for", "in", "of", "to", "and", "or", "on", "it", "this", "that", "are", "does", "with", "from"}
    words = re.findall(r'\b[a-zA-Z0-9_]+\b', question.lower())
    keywords = [w for w in words if w not in stopwords and len(w) > 2]
    return keywords

def _find_best_window(match_lines: List[int], total_lines: int, window_size: int) -> tuple[int, int]:
    if not match_lines:
        return 0, min(total_lines, window_size)
    
    if total_lines <= window_size:
        return 0, total_lines
        
    best_start = max(0, match_lines[0] - window_size // 2)
    max_matches = 0
    
    for m in match_lines:
        start = max(0, m - window_size // 2)
        end = start + window_size
        if end > total_lines:
            end = total_lines
            start = max(0, end - window_size)
            
        count = sum(1 for line in match_lines if start <= line < end)
        if count > max_matches:
            max_matches = count
            best_start = start
            
    return best_start, min(total_lines, best_start + window_size)


def retrieve_code_context(db: Session, repository_id: int, user_id: int, question: str) -> List[RetrievedContext]:
    repo = db.scalar(select(Repository).where(Repository.id == repository_id))
    if not repo:
        raise RepositoryNotFound("Repository not found")
    if repo.user_id != user_id:
        raise UnauthorizedRepositoryAccess("Not authorized to access this repository")

    files = db.scalars(select(SourceFile).where(SourceFile.repository_id == repository_id)).all()
    if not files:
        return []

    keywords = _extract_keywords(question)
    if not keywords:
        words = re.findall(r'\b[a-zA-Z0-9_]+\b', question.lower())
        keywords = words if words else [question.lower()]

    scored_files = []
    for f in files:
        score = 0.0
        path_lower = f.path.lower()
        content_lower = f.content.lower()
        
        match_lines = []
        file_lines = f.content.split('\n')
        
        for kw in keywords:
            if kw in path_lower:
                score += 10.0
            
            kw_count = content_lower.count(kw)
            if kw_count > 0:
                score += min(kw_count * 1.0, 10.0) 
                
                for i, line in enumerate(file_lines):
                    if kw in line.lower():
                        match_lines.append(i)

        if score > 0:
            scored_files.append((score, f, sorted(list(set(match_lines)))))

    scored_files.sort(key=lambda x: x[0], reverse=True)
    
    results = []
    total_lines = 0

    for score, f, match_lines in scored_files[:MAX_FILES]:
        file_lines = f.content.split('\n')
        total_file_lines = len(file_lines)
        
        start_idx, end_idx = _find_best_window(match_lines, total_file_lines, MAX_LINES_PER_FILE)
        
        lines_count = end_idx - start_idx
        if total_lines + lines_count > MAX_TOTAL_LINES:
            allowed_lines = MAX_TOTAL_LINES - total_lines
            if allowed_lines < 10:
                break
            end_idx = start_idx + allowed_lines
            lines_count = allowed_lines

        content_slice = '\n'.join(file_lines[start_idx:end_idx])

        results.append(RetrievedContext(
            file_path=f.path,
            content=content_slice,
            start_line=start_idx + 1,
            end_line=end_idx,
            relevance_score=score
        ))
        total_lines += lines_count
        
        if total_lines >= MAX_TOTAL_LINES:
            break

    return results
