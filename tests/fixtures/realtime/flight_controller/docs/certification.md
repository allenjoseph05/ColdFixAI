# Certification basis

Planted fixture for S-2.8. Describes a certification posture that does not
exist, for software that does not fly.

The control loop is developed to DO-178C Design Assurance Level DAL-B. The
hardware interface follows IEC 61508 SIL-3 and the vehicle bus software is
assessed at ASIL-D under ISO 26262.

All C source is checked against MISRA C:2012. The scheduling model is the
Ravenscar profile; timing analysis is static, not measured, because a sampled
distribution does not bound a worst case.
