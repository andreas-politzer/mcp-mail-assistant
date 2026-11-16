# 0006: Approval outside the chat

Status: planned. Accepted as direction, not implemented.

Context: the server cannot prove that the final "yes" in the conversation came from a human (see README, "Honest limits").

Decision: recipients who are not the owner's own mailboxes require an approval that is bound to exactly one draft, can be used once, expires quickly, and is given through a channel the assistant cannot use. It sits in front of the existing atomic claim; claim, limits, the single SMTP attempt and the states `sent`, `failed` and `unknown` stay unchanged. The owner's own addresses keep the current flow.

Condition: other recipients are not enabled until the stage is built and acceptable in daily use. A throw-away prototype outside this repository comes first.

Not decided: technology, origin, how own and other recipients are represented, phone and desktop coverage.
