/** Scrollable, append-only provider text. Following is local to this panel. */
export class ThinkingContent {
  constructor() {
    this.region = document.createElement('div');
    this.region.className = 'thinking-preview'; this.region.tabIndex = 0;
    this.region.setAttribute('role', 'region'); this.region.setAttribute('aria-label', 'Model reasoning');
    this.latest = document.createElement('button'); this.latest.type = 'button';
    this.latest.className = 'thinking-latest'; this.latest.textContent = '↓ Latest thinking'; this.latest.hidden = true;
    this.follow = true; this.previousTop = 0; this.text = '';
    this.region.addEventListener('scroll', () => {
      if (!this.region.clientHeight) return;
      const top = this.region.scrollTop;
      if (top < this.previousTop - 1) this.follow = false;
      else if (this.atBottom()) this.follow = true;
      this.previousTop = top; this.sync();
    }, {passive: true});
    this.region.addEventListener('wheel', e => { if (e.deltaY < 0) this.stopFollowing(); }, {passive: true});
    this.region.addEventListener('keydown', e => { if (['ArrowUp', 'PageUp', 'Home'].includes(e.key)) this.stopFollowing(); });
    this.region.addEventListener('touchstart', e => { this.touchY = e.touches[0]?.clientY; }, {passive: true});
    this.region.addEventListener('touchmove', e => { if (e.touches[0]?.clientY > this.touchY) this.stopFollowing(); }, {passive: true});
    this.latest.onclick = () => { this.follow = true; this.pin(); this.sync(); };
  }
  atBottom() { return this.region.scrollHeight - this.region.scrollTop - this.region.clientHeight < 28; }
  stopFollowing() { this.follow = false; this.sync(); }
  sync() {
    const overflow = this.region.scrollHeight > this.region.clientHeight;
    this.region.dataset.follow = String(this.follow); this.region.dataset.overflow = String(overflow);
    this.latest.hidden = this.follow || !overflow;
  }
  pin() {
    cancelAnimationFrame(this.frame);
    this.frame = requestAnimationFrame(() => {
      if (!this.follow || !this.region.isConnected || !this.region.clientHeight) return;
      const selection = getSelection();
      if (selection && !selection.isCollapsed && (this.region.contains(selection.anchorNode) || this.region.contains(selection.focusNode))) {
        this.stopFollowing(); return;
      }
      this.region.scrollTop = this.region.scrollHeight;
      this.previousTop = this.region.scrollTop; this.sync();
    });
  }
  update(text, animate) {
    if (text !== this.text) {
      const append = text.startsWith(this.text);
      const delta = append ? text.slice(this.text.length) : text;
      if (!append) this.region.replaceChildren();
      if (delta) {
        const chunk = document.createElement('span'); chunk.textContent = delta;
        chunk.className = animate ? 'thinking-chunk' : 'thinking-chunk settled';
        this.region.append(chunk);
      }
      this.text = text;
    }
    this.sync(); if (this.follow) this.pin();
  }
}
