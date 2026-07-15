// Phase 0 eval: does Coccinelle find LOCKED_REGION structurally?
// Matches a spin_lock/spin_unlock pair on the same lock expression within
// one function — the structural family behind the LOCKED_REGION pattern.
virtual report

@r@
expression E;
position p1, p2;
@@
spin_lock@p1(E);
...
spin_unlock@p2(E);

@script:python depends on report@
p1 << r.p1;
p2 << r.p2;
@@
print("LOCKED_REGION %s:%s-%s" % (p1[0].file, p1[0].line, p2[0].line))
