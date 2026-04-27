import React from 'react';
import { AlertCircle, CheckCircle2, ChevronRight, Loader2 } from 'lucide-react';

export const INITIAL_PHASES = [
  { id: 'brief', label: 'Briefing', status: 'pending', message: '' },
  { id: 'planning', label: 'Planning', status: 'pending', message: '' },
  { id: 'searching', label: 'Searching', status: 'pending', message: '' },
  { id: 'compressing', label: 'Synthesis', status: 'pending', message: '' },
  { id: 'reporting', label: 'Reporting', status: 'pending', message: '' },
];

export function phaseStatusIcon(status) {
  switch (status) {
    case 'done':
      return <CheckCircle2 className="dr-icon done" />;
    case 'active':
      return <Loader2 className="dr-icon active spin" />;
    case 'error':
      return <AlertCircle className="dr-icon error" />;
    default:
      return <ChevronRight className="dr-icon pending" />;
  }
}

export function phaseRowClass(status) {
  switch (status) {
    case 'done':
      return 'done';
    case 'active':
      return 'active';
    case 'error':
      return 'error';
    default:
      return 'pending';
  }
}

