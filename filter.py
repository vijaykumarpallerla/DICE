import re

def should_keep_job(job_title, filter_string):
    """
    Returns True if the job title contains at least one of the filter keywords.
    Returns True if filter_string is empty (no filtering).
    """
    if not filter_string or not filter_string.strip():
        return True
    
    # Clean up the filters: split by comma, lowercase, and remove extra spaces
    filters = [f.strip().lower() for f in filter_string.split(',') if f.strip()]
    
    if not filters:
        return True
        
    job_title_lower = job_title.lower()
    
    # Check if ANY filter word exists in the job title
    for f in filters:
        # We use re.search with \b (word boundary) to match full words
        # This prevents "Java" matching in "Javascript" unless you want that.
        # But for tech, usually a simple 'in' is safer for things like "J2EE/Java"
        if f in job_title_lower:
            return True
            
    return False
