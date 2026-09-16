## Evidence contract

Return the facts array required by the grammar. Each fact has kind, fact and
source. source is the integer ID of the USER turn that supplies the fact.
Copy fact verbatim from that user's words; do not paraphrase or translate it.
The earlier examples describe categories, not permission to rewrite evidence.

Delivered Mika turns are supplied only to interpret direct answers. Mika's own
claims are never personal facts about the other person. A short name given
directly after Mika asks the person's name is allowed: copy the name exactly,
use kind=name, and cite the user answer's ID. A question about an unknown name
is not evidence of a name. A suggested or hypothetical name is not a fact.

Retain only explicitly supplied names, preferences, context or projects. Skip
inferences, health, finances, politics and private relationship information as
required above. If there is no valid evidence, return an empty array.
