try {
  document.documentElement.className = localStorage.getItem('mdh_theme') === 'dark' ? 'dark' : 'light';
} catch { /* The document defaults to light when storage is unavailable. */ }
