// One stacking order for everything on the desktop that can be clicked into the foreground:
// application tiles, clock, sticky note, Funki and the desktop icons.
// The element clicked last is the last in the list and lies in front. Elements that were never
// clicked and are not in the list lie at the very back (level 1).
// All levels stay far below the task bars (70), menus and dialogs (90 and up).

export const STACK_BASE = 10

export function raised(stack: readonly string[], id: string): string[] {
  return stack[stack.length - 1] === id ? (stack as string[]) : [...stack.filter((x) => x !== id), id]
}

export function levelOf(stack: readonly string[], id: string): number {
  const i = stack.indexOf(id)
  return i < 0 ? 1 : STACK_BASE + i
}
