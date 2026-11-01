import type { ReactNode } from 'react';

import { IconClose, IconShield, IconWarning } from './Icons';

interface PermissionBannerProps {
  tone: 'info' | 'warning' | 'danger';
  title: string;
  children?: ReactNode;
  onDismiss?: () => void;
  action?: { label: string; onClick: () => void };
}

export function PermissionBanner({ tone, title, children, onDismiss, action }: PermissionBannerProps) {
  return (
    <div className={`banner banner--${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      <span className="banner__icon">
        {tone === 'info' ? <IconShield size={16} /> : <IconWarning size={16} />}
      </span>
      <div className="banner__body">
        <strong>{title}</strong>
        {children && <p>{children}</p>}
      </div>
      {action && (
        <button type="button" className="button button--tiny" onClick={action.onClick}>
          {action.label}
        </button>
      )}
      {onDismiss && (
        <button type="button" className="icon-button" onClick={onDismiss} aria-label="Dismiss">
          <IconClose size={14} />
        </button>
      )}
    </div>
  );
}
