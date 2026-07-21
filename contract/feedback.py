"""
The messages the sandbox returns to the LLM when something goes wrong.

Each message states what happened and what to do next, so the next
iteration can fix the problem instead of guessing.
"""

# prefix of the observation when the code called final_answer():
# the sandbox emits it, the loop stops on it
FINAL_PREFIX = "__final__:"

NO_CODE = (
    "No python code block found in your reply. "
    "Reply with a short thought and one ```python code block."
)
NO_OUTPUT = (
    "The code ran without errors and printed nothing, so no tool was "
    "called. Check that the ''' of your code string closes before the "
    "call, and wrap the value you want to see in print(...)."
)
MALFORMED_BLOCK = (
    "[Your code block was never closed, so it was read to the end of "
    "the reply and run anyway. Close it with ``` next time.]\n"
)
# placed in the history instead of an empty model reply: providers
# reject an assistant message with no content
EMPTY_REPLY = "(no output)"
BLOCKED_IMPORT = (
    "Import of '{name}' is not authorized. Use only: {allowed}."
)
BLOCKED_PATH = (
    "Path '{path}' is outside the allowed directories: {allowed}."
)
BLOCKED_BUILTIN = (
    "The builtin '{name}' is disabled here. Solve it with plain code."
)
TIMEOUT = (
    "Execution stopped after {seconds}s. Write a faster approach."
)
MEMORY = (
    "Execution stopped: over {mb} MB of memory. Use less memory."
)
TRUNCATED = (
    "[output truncated at {chars} characters - print less next time]"
)
