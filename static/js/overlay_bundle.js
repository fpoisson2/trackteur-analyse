if (!customElements.get('mce-autosize-textarea')) {
  class AutosizeTextarea extends HTMLTextAreaElement {
    connectedCallback() {
      this.style.resize = 'none';
      const resize = () => {
        this.style.height = 'auto';
        this.style.height = `${this.scrollHeight}px`;
      };
      this.addEventListener('input', resize);
      resize();
    }
  }
  customElements.define('mce-autosize-textarea', AutosizeTextarea, { extends: 'textarea' });
}
