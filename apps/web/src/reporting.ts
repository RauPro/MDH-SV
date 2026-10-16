import { publicInstance } from './instance';

export const REPOSITORY_URL = `https://github.com/${publicInstance.public_repository}`;
export const TECHNICAL_REPORT_URL = `${REPOSITORY_URL}/security/advisories/new`;
export const EDITORIAL_REPORT_EMAIL = publicInstance.editorial_email;
// Private contact links deliberately carry no dossier data or prefilled message.
export const EDITORIAL_REPORT_URL = `mailto:${EDITORIAL_REPORT_EMAIL}`;
export const RECORD_CORRECTION_URL = `${REPOSITORY_URL}/issues/new?template=record-correction.yml&title=Correcci%C3%B3n%20de%20registro`;
export const REPORTING_RESPONSIBLE = publicInstance.reporting_responsible;
