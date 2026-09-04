"""
The AI layer.

Its one architectural rule, from ARCHITECTURE.md section G: the model does not
generate SQL, it generates the report IR. Everything else here follows from
that.

Because the model has no channel that carries SQL, prompt injection cannot
produce a dangerous query -- the worst a hostile instruction can do is ask for a
table the user may not read, which the resolver rejects exactly as it rejects a
person asking for it. Row-level permissions, column masking, the credential
exclusions and fan-out correction are inherited rather than reimplemented,
because the IR goes through the same compiler as a report somebody built by
hand.

The model is sent the shape of the database and never its contents.
"""
