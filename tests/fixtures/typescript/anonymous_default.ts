// Anonymous default-export class: the method must still carry a class segment
// (`default.m`) rather than silently becoming a top-level `m`.

export default class {
  m(): number {
    return 1;
  }
}
