"""Render reviewed fork destinations locally, without enabling any automation.

Usage: python3.13 scripts/configure_instance.py --from /absolute/instance.json
       python3.13 scripts/configure_instance.py --check
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/pipeline/src'))
instance_module = importlib.import_module('mdh_pipeline.instance')
InstanceConfigurationError = instance_module.InstanceConfigurationError
load_instance = instance_module.load_instance

START = '<!-- mdh-instance-reporting:start -->'
END = '<!-- mdh-instance-reporting:end -->'
MAX_BYTES = 1_000_000
# Role, explicit checkout repositories, literal repository guards. This reviewed
# inventory makes a new or restructured workflow an explicit maintenance change.
WORKFLOWS = {
    '.github/workflows/ci.yml': ('public', 0, 0),
    '.github/workflows/deploy_web.yml': ('public', 0, 0),
    '.github/workflows/monitor_cron.yml': ('public', 0, 0),
    '.github/workflows/publication-pr.yml': ('public', 0, 1),
    '.github/workflows/news-exception.yml': ('public', 0, 1),
    '.github/workflows/refresh-history.yml': ('public', 0, 1),
    '.github/workflows/release.yml': ('public', 0, 0),
    'ops/history/refresh-history.yml': ('public', 0, 1),
    'ops/history/review-history.yml': ('private', 2, 1),
    'ops/official-statistics-review/monitor.yml': ('private', 2, 1),
    'ops/official-statistics-review/review.yml': ('private', 2, 1),
    'ops/review-queue/.github/workflows/collect.yml': ('private', 1, 1),
    'ops/review-queue/.github/workflows/extract.yml': ('private', 2, 1),
    'ops/review-queue/.github/workflows/extract-fallback.yml': ('private', 2, 1),
    'ops/review-queue/.github/workflows/review.yml': ('private', 1, 1),
    'ops/review-queue/.github/workflows/review-scope.yml': ('private', 2, 1),
    'ops/review-queue/.github/workflows/publish-locations.yml': ('private', 4, 2),
    'ops/review-queue/.github/workflows/reconcile-news-issues.yml': ('private', 2, 1),
}
FORMS = {
    '.github/ISSUE_TEMPLATE/news-correction.yml': 'escribe directamente a',
    '.github/ISSUE_TEMPLATE/record-correction.yml': 'escribe a',
}
CONTACTS = '.github/ISSUE_TEMPLATE/config.yml'
OWNERS = '.github/CODEOWNERS'
SECURITY = 'SECURITY.md'
STATIC_PATHS = (*WORKFLOWS, *FORMS, CONTACTS, OWNERS, SECURITY)


class ConfigurationError(ValueError):
    """Only controlled diagnostic codes; never print untrusted file contents."""


def _read(root, relative):
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ConfigurationError('invalid_checkout_root')
    path = root
    for part in Path(relative).parts:
        if part in {'', '.', '..'}:
            raise ConfigurationError('invalid_configuration_path')
        path /= part
        if path.is_symlink():
            raise ConfigurationError('configuration_symlink_rejected')
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ConfigurationError('missing_or_oversized_configuration_surface')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ConfigurationError('oversized_configuration_surface')
    raw.decode('utf-8')
    return raw, stat.S_IMODE(path.stat().st_mode)


def _sub(pattern, replacement, content, *, count):
    result, found = re.subn(pattern, replacement, content, flags=re.MULTILINE)
    if found != count:
        raise ConfigurationError('configuration_surface_structure_changed')
    return result


def render_workflow(relative, content, instance):
    role, checkouts, guards = WORKFLOWS[relative]
    repository = instance.private_repository if role == 'private' else instance.public_repository
    content = _sub(r'^([ \t]+repository:[ \t]*)[^\r\n]+$',
                   lambda match: match[1] + instance.public_repository, content, count=checkouts)
    content = _sub(r'''github\.repository[ \t]*==[ \t]*(['"])[^'"\r\n]+\1''',
                   lambda _: "github.repository == '" + repository + "'", content, count=guards)
    if guards and "vars.MDH_AUTOMATION_ENABLED == 'true'" not in content:
        raise ConfigurationError('automation_opt_in_guard_missing')
    if role == 'private' and 'github.event.repository.private == true' not in content:
        raise ConfigurationError('private_repository_guard_missing')
    return content


def responsible_url(responsible):
    name = responsible.removeprefix('@')
    if '/' in name:
        owner, team = name.split('/', 1)
        return f'https://github.com/orgs/{owner}/teams/{team}'
    return 'https://github.com/' + name


def reporting_block(instance):
    repository = 'https://github.com/' + instance.public_repository
    responsible = f'[{instance.reporting_responsible}]({responsible_url(instance.reporting_responsible)})'
    email = f'[{instance.editorial_email}](mailto:{instance.editorial_email})'
    return f'''## Vulnerabilidades técnicas

Use [Reportar una vulnerabilidad de forma privada en GitHub]({repository}/security/advisories/new). Requiere una cuenta de GitHub. Responsable de recepción y seguimiento: {responsible}.

El mantenedor debe habilitar y verificar el reporte privado de vulnerabilidades en su repositorio y comprobar la recepción del correo editorial antes de activar una instancia. Configurar estos destinos no demuestra que los canales estén operativos.

Describa el componente, el impacto y los pasos para reproducirlo con datos ficticios. No incluya claves reales, identidades de víctimas ni documentos privados. Los reportes se tramitan en el aviso privado; los detalles que se publiquen después deben excluir datos sensibles. Los issues, comentarios de PR y discusiones ordinarias son públicos.

## Correcciones editoriales sensibles y retiradas

Escriba a {email} para correcciones sensibles o solicitudes de retirada de contenido publicado. Responsable de recepción y seguimiento: {responsible}. En el primer contacto incluya sólo la URL o el identificador público y una descripción mínima; no adjunte documentos de identidad, testimonios privados ni datos de terceras personas. El responsable solicitará por la misma vía sólo la información necesaria.

La recepción y la decisión son humanas. El correo no se incorpora automáticamente al pipeline, a Gemini ni a un issue público. Para errores no sensibles, utilice el [formulario público]({repository}/issues/new/choose) con el identificador y una explicación general. No es necesario abrir una solicitud pública para obtener atención privada. Véase el [procedimiento de recepción y seguimiento](docs/PRIVATE-REPORTING.md).

Para ambos canales privados, el objetivo de primera respuesta es de **7 días naturales**. Es orientativo, no un SLA ni un plazo de resolución garantizado; no hay atención permanente. Los servicios de correo y GitHub tratan datos según sus propias políticas y estas vías no garantizan anonimato.

El portal público no recibe denuncias, no proporciona atención de emergencias y no promete anonimato de red. Las preferencias de tema se almacenan en el navegador; las consultas de búsqueda se realizan localmente. Los enlaces externos y el proveedor de alojamiento tienen políticas propias.'''


def render_security(content, instance):
    if content.count(START) != 1 or content.count(END) != 1:
        raise ConfigurationError('reporting_markers_missing_or_ambiguous')
    before, rest = content.split(START, 1)
    if END in before:
        raise ConfigurationError('reporting_markers_out_of_order')
    _, after = rest.split(END, 1)
    return before + START + '\n\n' + reporting_block(instance) + '\n\n' + END + after


def render_form(relative, content, instance):
    email = f'[{instance.editorial_email}](mailto:{instance.editorial_email})'
    editorial = (f'Para correcciones sensibles o retiradas, {FORMS[relative]} {email}, '
                 f'atendido por {instance.reporting_responsible}. No necesitas abrir un issue público. '
                 'Primer contacto: sólo URL o ID público y descripción mínima, sin adjuntos personales.')
    content = _sub(r'^([ \t]*)Para correcciones sensibles o retiradas,[^\r\n]*$',
                   lambda match: match[1] + editorial, content, count=1)
    technical = ('Para vulnerabilidades técnicas usa el [reporte privado de GitHub]('
                 f'https://github.com/{instance.public_repository}/security/advisories/new).')
    return _sub(r'^([ \t]*)Para vulnerabilidades técnicas[^\r\n]*$',
                lambda match: match[1] + technical, content, count=1)


def render_contacts(content, instance):
    # Render exact named routes, rather than substituting a repository substring
    # that may prefix the private repository or the newly configured slug.
    repository = 'https://github.com/' + instance.public_repository
    for route in ('/blob/main/SECURITY.md#correcciones-editoriales-sensibles-y-retiradas',
                  '/security/advisories/new'):
        content = _sub(r'^([ \t]*url:[ \t]*)https://github\.com/[^/\s]+/[^/\s]+'
                       + re.escape(route) + r'$',
                       lambda match, route=route: match[1] + repository + route, content, count=1)
    return _sub(r'^([ \t]*about:[ \t]*)Correo privado [^\r\n]*$',
                lambda match: match[1] + f'Correo privado {instance.editorial_email}, '
                f'atendido por {instance.reporting_responsible}. No necesita abrir un issue público.',
                content, count=1)


def plan_changes(root, instance, *, replace_instance):
    root = Path(root)
    originals = {relative: _read(root, relative) for relative in ('instance.json', *STATIC_PATHS)}
    discovered = set()
    for folder in ('.github/workflows', 'ops'):
        for suffix in ('*.yml', '*.yaml'):
            discovered.update(str(path.relative_to(root)) for path in (root / folder).rglob(suffix))
    if discovered != set(WORKFLOWS):
        raise ConfigurationError('workflow_inventory_changed')
    prepared = {}
    for relative in STATIC_PATHS:
        content = originals[relative][0].decode('utf-8')
        if relative in WORKFLOWS:
            result = render_workflow(relative, content, instance)
        elif relative in FORMS:
            result = render_form(relative, content, instance)
        elif relative == CONTACTS:
            result = render_contacts(content, instance)
        elif relative == OWNERS:
            result = _sub(r'^\*[ \t]+[^\r\n]+$',
                          lambda _: '* ' + instance.reporting_responsible, content, count=1)
        else:
            result = render_security(content, instance)
        prepared[relative] = result.encode('utf-8')
    if replace_instance:
        prepared['instance.json'] = (json.dumps(asdict(instance), indent=2, ensure_ascii=False) + '\n').encode()
    changes = {relative: raw for relative, raw in prepared.items() if raw != originals[relative][0]}
    return originals, changes


def _stage(path, raw, mode):
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.instance-', dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        staged.chmod(mode)
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def apply_changes(root, originals, changes):
    """Prepare every file, verify unchanged originals, then atomically replace.

Filesystem errors trigger best-effort restoration of this operation's changes;
an independently modified file is never overwritten during restoration.
"""
    root = Path(root)
    staged, backups, applied = {}, {}, []
    try:
        for relative, raw in changes.items():
            original, mode = originals[relative]
            staged[relative] = _stage(root / relative, raw, mode)
            backups[relative] = _stage(root / relative, original, mode)
        for relative, (raw, _) in originals.items():
            if _read(root, relative)[0] != raw:
                raise ConfigurationError('configuration_changed_during_preparation')
        for relative in changes:
            staged[relative].replace(root / relative)
            applied.append(relative)
    except (OSError, ConfigurationError):
        for relative in reversed(applied):
            try:
                if _read(root, relative)[0] == changes[relative]:
                    backups[relative].replace(root / relative)
            except (OSError, ConfigurationError):
                pass
        raise
    finally:
        for path in (*staged.values(), *backups.values()):
            path.unlink(missing_ok=True)


def configure(root=ROOT, *, source=None, check=False):
    root = Path(root)
    # Deliberately ignore MDH_INSTANCE_FILE: this command manages this checkout.
    # The --from argument is the only input override, and the loader requires it
    # to be an explicit absolute, regular, validated file.
    instance = load_instance(source if source is not None else root / 'instance.json', environ={})
    originals, changes = plan_changes(root, instance, replace_instance=source is not None)
    if not check:
        apply_changes(root, originals, changes)
    return tuple(sorted(changes))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='source', type=Path, help='Absolute reviewed non-secret configuration JSON')
    parser.add_argument('--check', action='store_true', help='Check coherence without writing any files')
    args = parser.parse_args(argv)
    if args.source is None and not args.check:
        parser.error('Specify --from or --check')
    try:
        changes = configure(source=args.source, check=args.check)
    except (ConfigurationError, InstanceConfigurationError, OSError, UnicodeError):
        print('Instance configuration failed; check the validated input and reviewed template structure.',
              file=sys.stderr)
        return 2
    if args.check and changes:
        print('Instance configuration drift: ' + ', '.join(changes), file=sys.stderr)
        return 1
    print('Instance configuration is coherent.' if args.check else
          f'Updated {len(changes)} local files. Review the diff; automation remains opt-in.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
