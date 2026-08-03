import {
  AlertTriangle,
  Check,
  ChevronRight,
  LoaderCircle,
  Search,
  X,
} from "lucide-react";

export function Button({ children, tone = "primary", icon: Icon, className = "", ...props }) {
  return (
    <button className={`button button--${tone} ${className}`} {...props}>
      {Icon && <Icon size={16} strokeWidth={2.2} />}
      <span>{children}</span>
    </button>
  );
}

export function IconButton({ label, icon: Icon, tone = "ghost", className = "", ...props }) {
  return (
    <button className={`icon-button icon-button--${tone} ${className}`} aria-label={label} title={label} {...props}>
      <Icon size={18} />
    </button>
  );
}

export function PageHeader({ eyebrow, title, description, actions }) {
  return (
    <header className="page-header">
      <div>
        <div className="page-header__eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}

export function Panel({ title, eyebrow, actions, children, className = "" }) {
  return (
    <section className={`panel ${className}`}>
      {(title || actions) && (
        <div className="panel__head">
          <div>
            {eyebrow && <span className="panel__eyebrow">{eyebrow}</span>}
            {title && <h2>{title}</h2>}
          </div>
          {actions && <div className="panel__actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Field({ label, hint, children, className = "" }) {
  return (
    <label className={`field ${className}`}>
      <span className="field__label">{label}</span>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </label>
  );
}

export function Switch({ checked, onChange, label, description }) {
  return (
    <label className="switch-row">
      <button
        type="button"
        className={`switch ${checked ? "is-on" : ""}`}
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
      >
        <span />
      </button>
      <span>
        <strong>{label}</strong>
        {description && <small>{description}</small>}
      </span>
    </label>
  );
}

export function Status({ children, tone = "neutral", icon }) {
  const Icon = icon || (tone === "ok" ? Check : tone === "error" ? AlertTriangle : null);
  return (
    <div className={`status status--${tone}`}>
      {Icon && <Icon size={15} />}
      <span>{children}</span>
    </div>
  );
}

export function SearchInput({ value, onChange, placeholder }) {
  return (
    <div className="search-input">
      <Search size={17} />
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
      {value && (
        <button type="button" aria-label="清除搜索" onClick={() => onChange("")}>
          <X size={15} />
        </button>
      )}
    </div>
  );
}

export function Empty({ title, description, action }) {
  return (
    <div className="empty">
      <div className="empty__mark">···</div>
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}

export function Loading({ label = "正在读取" }) {
  return (
    <div className="loading">
      <LoaderCircle className="spin" size={20} />
      {label}
    </div>
  );
}

export function Metric({ label, value, suffix, tone = "blue" }) {
  return (
    <div className={`metric metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {suffix && <small>{suffix}</small>}
    </div>
  );
}

export function LinkRow({ icon: Icon, title, description, onClick, meta }) {
  return (
    <button className="link-row" type="button" onClick={onClick}>
      <span className="link-row__icon"><Icon size={19} /></span>
      <span className="link-row__copy"><strong>{title}</strong><small>{description}</small></span>
      {meta && <span className="link-row__meta">{meta}</span>}
      <ChevronRight size={17} />
    </button>
  );
}
