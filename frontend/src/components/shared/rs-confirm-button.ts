import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("rmc-confirm-button")
export class RsConfirmButton extends LitElement {
  @property({ type: String }) public label = "";
  @property({ type: String }) public confirmMessage = "";
  @property({ type: Boolean }) public disabled = false;
  @property({ type: Boolean }) public destructive = false;

  static styles = css`
    :host {
      display: block;
    }

    .confirm-btn {
      background: transparent;
      border: 1px solid var(--divider-color);
      border-radius: 8px;
      padding: 8px 16px;
      font-size: 14px;
      font-family: inherit;
      cursor: pointer;
      color: var(--primary-text-color);
      transition: all 0.2s;
    }

    .confirm-btn.destructive {
      border-color: var(--error-color);
      color: var(--error-color);
    }

    .confirm-btn.destructive:hover:not([disabled]) {
      background: var(--error-color);
      color: #fff;
    }

    .confirm-btn[disabled] {
      opacity: 0.5;
      cursor: not-allowed;
    }
  `;

  render() {
    return html`
      <button
        class="confirm-btn ${this.destructive ? "destructive" : ""}"
        ?disabled=${this.disabled}
        @click=${this._onClick}
      >
        ${this.label}
      </button>
    `;
  }

  private _onClick() {
    if (this.disabled) return;
    if (this.confirmMessage && !confirm(this.confirmMessage)) return;
    this.dispatchEvent(
      new CustomEvent("confirmed", {
        bubbles: true,
        composed: true,
      }),
    );
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "rmc-confirm-button": RsConfirmButton;
  }
}
