"""Data-source adapters — one module per upstream, sharing one interface.

Lands here as the sites are factored: the ESPN client (two-host origin
handling, retry/backoff, incremental fetch) is the first and is shared by
WNBA and NCAAW; FIBA gets its own; a league with no external validator
simply configures none.

Deliberately empty at the restructure commit. The files moved first and
prove byte-identical output; the extraction follows in the commit that
needs it, so a move and a rewrite are never being debugged at once.
"""
