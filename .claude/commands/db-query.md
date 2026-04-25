Run a read-only SQL query against the ArXistant database.

Query: $ARGUMENTS

Database: data/arxistant.db

If no argument provided, run these default queries:

1. Overview:
```sql
SELECT 'Papers' as tbl, COUNT(*) as cnt FROM papers
UNION ALL SELECT 'Analyzed', COUNT(*) FROM papers WHERE quality_score IS NOT NULL
UNION ALL SELECT 'Read', COUNT(*) FROM reading_notes
UNION ALL SELECT 'Active tree nodes', COUNT(*) FROM knowledge_tree WHERE status='active'
UNION ALL SELECT 'Tree links', COUNT(*) FROM paper_tree_links
UNION ALL SELECT 'Sessions', COUNT(*) FROM chat_sessions;
```

2. Recent papers (last 7 days):
```sql
SELECT arxiv_id, substr(title,1,60) as title, quality_score, first_seen_at
FROM papers WHERE first_seen_at >= datetime('now','-7 days')
ORDER BY first_seen_at DESC LIMIT 20;
```

3. Top quality papers:
```sql
SELECT arxiv_id, substr(title,1,60) as title, quality_score
FROM papers WHERE quality_score >= 4
ORDER BY quality_score DESC LIMIT 10;
```

If argument provided:
- Run it as SQL.
- IMPORTANT: Only allow SELECT queries. If the query contains INSERT, UPDATE, DELETE, DROP, ALTER, or CREATE, refuse and warn the user.

Run with:
```bash
sqlite3 -header -column data/arxistant.db "QUERY"
```

Format results in a readable way.
