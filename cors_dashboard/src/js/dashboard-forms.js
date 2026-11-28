// Entry point for dashboard form modules; the build bundles these into one browser asset.
import './forms/license-provision.js';
import './forms/base-configuration.js';
import './forms/service-configuration.js';

window.CorsDashboardForms = {
    LicenseForm: window.LicenseForm,
    ProvisionForm: window.ProvisionForm,
    BaseConfigForm: window.BaseConfigForm,
    ServiceConfigForm: window.ServiceConfigForm,
};
