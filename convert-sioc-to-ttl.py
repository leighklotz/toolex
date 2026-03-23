#!/usr/bin/env python3

import rdflib

g = rdflib.Graph()
g.parse("scuttle-sioc.rdf", format="application/rdf+xml")
with open("scuttle-sioc.ttl", "w") as f:
    f.write(g.serialize(format="turtle"))
