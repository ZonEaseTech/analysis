"""Entity CTE factories.

Each module here owns one business object's SQL definition. Functions
return CTE bodies (`name AS (...)`) as strings — callers join with commas
to assemble a WITH clause.
"""
