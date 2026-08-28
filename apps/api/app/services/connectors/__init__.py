"""
External API connectors.

A connector's job is to turn somebody else's API into rows in a table, because
the report engine compiles SQL and cannot query HTTP. Once the rows are in a
table, everything else in this application already works on them: the schema
registry lists the columns, the builder offers them as fields, the compiler
joins them to operational data through the hybrid executor, and the credential
and masking rules apply exactly as they do everywhere else.

So a connector is deliberately small. It fetches, it names its columns, and it
hands rows over. It does not know what a report is.
"""
