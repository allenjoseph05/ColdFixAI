# task_tracker — the control for S-2.8

An ordinary web application, and the more important half of this fixture.

ADR 006's rule is that every defect carries a control or the detector learns to
say yes. `../flight_controller` is the defect. This is the control, and it is
deliberately packed with the vocabulary a naive real-time detector would fire
on:

- tasks with a **deadline**, including **hard deadlines** in the contractual sense
- a **priority** field whose highest value is **critical**
- a class named **Scheduler**, with a priority queue
- **real-time** updates, meaning a websocket pushes to the browser
- **mission-critical** work, meaning somebody will be annoyed

None of it is a timing guarantee. This application is exactly the kind of
software ColdFix exists to make faster, and the pinned development target in
ADR 011 is a helpdesk full of the same words. A screening that refuses this
refuses its own target on day one.

**Read this before widening any pattern in `realtime.py`.**
