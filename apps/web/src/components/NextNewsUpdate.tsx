import { useEffect, useState } from 'react';
import { archiveInstant } from '../newsArchive';
import { nextNewsAnalysis } from '../newsSchedule';

export function NextNewsUpdate() {
  const [next, setNext] = useState(() => nextNewsAnalysis(Date.now()));

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const refresh = () => {
      const upcoming = nextNewsAnalysis(Date.now());
      setNext(upcoming);
      clearTimeout(timer);
      timer = setTimeout(refresh, Math.max(1, new Date(upcoming).getTime() - Date.now() + 1));
    };
    const onVisibility = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    refresh();
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  return <p className="classification-schedule">
    <strong>Próxima actualización:</strong> análisis programado para <time dateTime={next}>{archiveInstant(next)}</time> (El Salvador).
    <span>Los resultados se publican al terminar; pueden retrasarse por la cuota o la disponibilidad de las fuentes.</span>
  </p>;
}
