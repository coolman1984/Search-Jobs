# Case study: AI agents that prove what they did (wad + WinSight)

**Problem:** Desktop "AI agents" click screen coordinates, miss, and report success anyway. In business that silent failure is the expensive part.

**Solution — wad (Windows Agent Desktop):** reads an application through its accessibility tree, gives every element a short ref or a durable selector (`role=Button name=Save`), acts through accessibility patterns instead of the mouse, and **proves each action worked**.

**Solution — WinSight:** one 5 MB executable with 34 tools that finds and fixes what makes Windows full, slow or broken. Every fix is reversible, and every tool is exposed to AI agents through an MCP server.

**Principle carried into every client project:** an agent proposes, a person approves anything sensitive, and every step leaves evidence.

**Repos:** github.com/coolman1984/win-agent-desktop · github.com/coolman1984/Performance
