targetScope = 'subscription'

@description('Deployment location. Defaults to resource group location')
param location string

@description('Workload/application name (used for resource naming)')
param appName string = 'aks-chaos-lab'

@description('Environment name (e.g., dev, test, prod)')
param environment string = 'dev'

@description('AKS node VM size')
param nodeVmSize string = 'Standard_D4ds_v5'

@description('Virtual network address prefix')
param vnetAddressPrefix string = '10.10.0.0/16'

@description('AKS subnet address prefix')
param aksSubnetPrefix string = '10.10.1.0/24'

@description('Private Endpoint subnet address prefix')
param peSubnetPrefix string = '10.10.2.0/24'

@description('AKS API Server subnet address prefix')
param aksApiSubnetPrefix string = '10.10.3.0/28'

@description('Kubernetes version for AKS (x.y or x.y.z).')
param kubernetesVersion string = '1.33'

@description('AKS SKU mode. Only "Base" is supported in this repository (see ADR-010: AKS Automatic is incompatible with chaos-mesh due to AKS Automatic Deployment Safeguards).')
@allowed([
  'Base'
])
param aksSkuName string = 'Base'

@description('Create Azure Monitor Workspace for Managed Prometheus (recommended)')
param enablePrometheusWorkspace bool = true

@description('Deploy Prometheus recording rules (Linux/UX) into AMW')
param enablePrometheusRecordingRules bool = true

@description('Deploy short-window app operational Prometheus alert rules for incident detection.')
param enablePrometheusAppOperationalAlerts bool = true

@description('Deploy Azure Monitor SLI resources for the chaos app. Requires Service Group permissions at the tenant root or specified parent service group.')
param enableAzureMonitorSli bool = false

@description('Parent Service Group resource ID for Azure Monitor SLI. Leave empty to use the tenant root service group.')
param azureMonitorSliParentServiceGroupId string = ''

@description('Existing Service Group resource ID for Azure Monitor SLI. When set, Bicep uses this Service Group directly instead of creating a per-environment child Service Group.')
param azureMonitorSliServiceGroupResourceId string = ''

@description('Deploy Data Collection pipeline (DCR/DCE/DCRA) for Managed Prometheus')
param enablePrometheusPipeline bool = true

@description('Deploy Azure Chaos Studio experiments for AKS (Chaos Mesh)')
param enableChaosExperiments bool = true

@description('Deploy Container Insights Data Collection Rule and Association')
param enableContainerInsights bool = true

@description('Enable container network logs collection in Container Insights (ACNS + Cilium required)')
param enableContainerNetworkLogs bool = true

@description('Deploy AKS control plane diagnostic logs (AKSAuditAdmin, AKSControlPlane) with Basic table plan (ADR-005)')
param enableAksDiagnostics bool = true

@description('Container Insights data collection preset (All, LogsAndEvents, Custom)')
@allowed([
  'All'
  'LogsAndEvents'
  'Custom'
])
param containerInsightsPreset string = 'Custom'

@description('Custom streams for Container Insights (used when containerInsightsPreset is Custom). ADR-003: Perf and InsightsMetrics excluded (covered by Managed Prometheus).')
param containerInsightsStreams array = [
  'Microsoft-ContainerLog'
  'Microsoft-ContainerLogV2'
  'Microsoft-KubeEvents'
  'Microsoft-KubePodInventory'
  'Microsoft-ContainerInventory'
  'Microsoft-ContainerNodeInventory'
  'Microsoft-KubeNodeInventory'
]

@description('Action Group resource ID for alerts (optional, leave empty for lab use)')
param actionGroupId string = ''

@description('Chaos experiments namespace (Kubernetes)')
param chaosNamespace string = 'chaos-lab'

@description('Chaos experiments target label (app=)')
param chaosAppLabel string = 'chaos-app'

@description('Chaos experiments default duration - FALLBACK ONLY for discrete experiments')
param chaosDuration string = 'PT5M'

// Common
var abbrs = loadJsonContent('./abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, appName, environment))
var tags = {
  'azd-env-name': environment
}
var resourceGroupName = '${abbrs.resourcesResourceGroups}${appName}-${environment}'
resource resourceGroup 'Microsoft.Resources/resourceGroups@2024-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

// Names (abbreviations-assisted following Azure CAF)
var virtualNetworkName = '${abbrs.networkVirtualNetworks}${appName}-${environment}'
var aksClusterName = 'aks-${appName}-${environment}'
var containerRegistryName = '${abbrs.containerRegistryRegistries}${resourceToken}'
var logAnalyticsWorkspaceName = '${abbrs.operationalInsightsWorkspaces}${appName}-${environment}'
var applicationInsightsName = '${abbrs.insightsComponents}${appName}-${environment}'
var azureMonitorWorkspaceName = 'amw-${appName}-${environment}'
var appAzureMonitorWorkspaceName = 'amw-app-${appName}-${environment}'
var redisEnterpriseName = '${abbrs.cacheRedis}${resourceToken}'
var chaosAppIdentityName = '${abbrs.managedIdentityUserAssignedIdentities}${appName}-chaos-app-${environment}'
var nodeResourceGroupName = '${resourceGroupName}-node'
var serviceAccountNamespace = 'chaos-lab'
var serviceAccountName = 'chaos-app-sa'
var fleetName = 'fleet-${appName}-${environment}'

// Modules
module network './modules/network.bicep' = {
  name: 'network'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    vnetName: virtualNetworkName
    vnetAddressPrefix: vnetAddressPrefix
    aksSubnetPrefix: aksSubnetPrefix
    peSubnetPrefix: peSubnetPrefix
    aksApiSubnetPrefix: aksApiSubnetPrefix
    resourceToken: resourceToken
  }
}

module azmonitorCore './modules/azmonitor/core.bicep' = {
  name: 'azmonitorCore'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    logAnalyticsName: logAnalyticsWorkspaceName
    applicationInsightsName: applicationInsightsName
    appAzureMonitorWorkspaceName: appAzureMonitorWorkspaceName
  }
}

module azureMonitorWorkspace './modules/prometheus/workspace.bicep' = if (enablePrometheusWorkspace) {
  name: 'prometheus'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    workspaceName: azureMonitorWorkspaceName
  }
}

#disable-next-line BCP318
var prometheusWorkspaceResourceId = enablePrometheusWorkspace ? azureMonitorWorkspace.outputs.workspaceId : ''
var enableAzureMonitorSliEffective = enableAzureMonitorSli && enablePrometheusWorkspace && enablePrometheusPipeline && enablePrometheusRecordingRules
var normalizedAzureMonitorSliParentServiceGroupId = azureMonitorSliParentServiceGroupId == 'none' ? '' : azureMonitorSliParentServiceGroupId
var normalizedAzureMonitorSliServiceGroupResourceId = azureMonitorSliServiceGroupResourceId == 'none' ? '' : azureMonitorSliServiceGroupResourceId
var useExistingAzureMonitorSliServiceGroup = !empty(normalizedAzureMonitorSliServiceGroupResourceId)
var azureMonitorSliServiceGroupNameBase = 'sg-${appName}-${environment}-${resourceToken}'
var azureMonitorSliServiceGroupName = substring(
  azureMonitorSliServiceGroupNameBase,
  0,
  min(90, length(azureMonitorSliServiceGroupNameBase))
)
var azureMonitorSliEffectiveParentServiceGroupId = empty(normalizedAzureMonitorSliParentServiceGroupId)
  ? '/providers/Microsoft.Management/serviceGroups/${tenant().tenantId}'
  : normalizedAzureMonitorSliParentServiceGroupId
var azureMonitorSliEffectiveServiceGroupName = useExistingAzureMonitorSliServiceGroup
  ? last(split(normalizedAzureMonitorSliServiceGroupResourceId, '/'))
  : azureMonitorSliServiceGroupName
var azureMonitorSliEffectiveServiceGroupId = useExistingAzureMonitorSliServiceGroup
  ? normalizedAzureMonitorSliServiceGroupResourceId
  : tenantResourceId('Microsoft.Management/serviceGroups', azureMonitorSliServiceGroupName)
var azureMonitorWorkspaceManagedResourceGroupName = 'MA_${azureMonitorWorkspaceName}_${location}_managed'
var azureMonitorSliIdentityName = '${abbrs.managedIdentityUserAssignedIdentities}${appName}-sli-${environment}'
var azureMonitorAvailabilitySliName = 'availability-${resourceToken}'
var azureMonitorLatencySliName = 'latency-${resourceToken}'

module prometheusPipeline './modules/prometheus/pipeline.bicep' = if (enablePrometheusWorkspace && enablePrometheusPipeline) {
  name: 'prometheusPipeline'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    prometheusWorkspaceId: prometheusWorkspaceResourceId
    aksId: aksCluster.outputs.aksId
    nameSuffix: '${appName}-${environment}'
  }
}

module prometheusRecordingRules './modules/prometheus/recording-rules.bicep' = if (enablePrometheusWorkspace && enablePrometheusRecordingRules) {
  name: 'prometheusRecordingRules'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    prometheusWorkspaceId: prometheusWorkspaceResourceId
    aksId: aksCluster.outputs.aksId
  }
}

module prometheusAlertRules './modules/prometheus/alert-rules.bicep' = if (enablePrometheusWorkspace) {
  name: 'prometheusAlertRules'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    prometheusWorkspaceId: prometheusWorkspaceResourceId
    aksId: aksCluster.outputs.aksId
    actionGroupId: actionGroupId
    enableAppOperationalAlerts: enablePrometheusAppOperationalAlerts
  }
}

module azureMonitorSliIdentity './modules/azmonitor/sli-identity.bicep' = if (enableAzureMonitorSliEffective) {
  name: 'azureMonitorSliIdentity'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    identityName: azureMonitorSliIdentityName
  }
}

module azureMonitorSliRbac './modules/azmonitor/sli-rbac.bicep' = if (enableAzureMonitorSliEffective) {
  name: 'azureMonitorSliRbac'
  scope: resourceGroup
  params: {
    #disable-next-line BCP318
    sliIdentityPrincipalId: azureMonitorSliIdentity.outputs.principalId
    prometheusWorkspaceName: azureMonitorWorkspaceName
    #disable-next-line BCP318
    prometheusDataCollectionRuleId: prometheusPipeline.outputs.dataCollectionRuleId
  }
}

module azureMonitorSliManagedDcrRbac './modules/azmonitor/sli-managed-dcr-rbac.bicep' = if (enableAzureMonitorSliEffective) {
  name: 'azureMonitorSliManagedDcrRbac'
  scope: az.resourceGroup(azureMonitorWorkspaceManagedResourceGroupName)
  dependsOn: [
    azureMonitorWorkspace
  ]
  params: {
    #disable-next-line BCP318
    sliIdentityPrincipalId: azureMonitorSliIdentity.outputs.principalId
    prometheusWorkspaceName: azureMonitorWorkspaceName
  }
}

resource azureMonitorSliServiceGroup 'Microsoft.Management/serviceGroups@2024-02-01-preview' = if (enableAzureMonitorSliEffective && !useExistingAzureMonitorSliServiceGroup) {
  scope: tenant()
  name: azureMonitorSliServiceGroupName
  properties: {
    displayName: '${appName} ${environment} SLO'
    parent: {
      resourceId: azureMonitorSliEffectiveParentServiceGroupId
    }
  }
}

module containerRegistry './modules/acr.bicep' = {
  name: 'acr'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    registryName: containerRegistryName
    vnetId: network.outputs.vnetId
    privateEndpointSubnetId: network.outputs.peSubnetId
    principalObjectIds: [aksCluster.outputs.kubeletObjectId]
  }
}

module aksCluster './modules/aks.bicep' = {
  name: 'aks'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    aksName: aksClusterName
    nodeVmSize: nodeVmSize
    aksSubnetId: network.outputs.aksSubnetId
    aksApiSubnetId: network.outputs.aksApiSubnetId
    kubernetesVersion: kubernetesVersion
    skuName: aksSkuName
    nodeResourceGroupName: nodeResourceGroupName
    logAnalyticsWorkspaceId: azmonitorCore.outputs.logAnalyticsId
    enableContainerNetworkLogs: enableContainerNetworkLogs
    actionGroupId: actionGroupId
  }
}

// Alert role assignments - subscription scope
// Separated into a module so principalId (a runtime value) can be passed as a parameter,
// which makes it a deploy-time value in the module context and usable in guid() for names.
module alertSubRoleAssignments './modules/alert-sub-role-assignments.bicep' = {
  name: 'alertSubRoleAssignments'
  params: {
    aksAlertPrincipalId: aksCluster.outputs.nodeOsAutoUpgradeAlertPrincipalId
    fleetAlertPrincipalId: fleetManager.outputs.pendingApprovalAlertPrincipalId
    enableFleet: true
  }
}

module fleetManager './modules/fleet.bicep' = {
  name: 'fleet'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    fleetName: fleetName
    aksClusterId: aksCluster.outputs.aksId
    fleetMemberName: 'base-cluster'
    updateStrategyName: 'base-manual-approval'
    updateStageName: 'base-stage'
    approvalDisplayName: 'Base cluster manual approval'
    actionGroupId: actionGroupId
  }
}

// Alert role assignments - resource group scope
module alertRgRoleAssignments './modules/alert-rg-role-assignments.bicep' = {
  name: 'alertRgRoleAssignments'
  scope: resourceGroup
  params: {
    aksAlertPrincipalId: aksCluster.outputs.nodeOsAutoUpgradeAlertPrincipalId
    fleetAlertPrincipalId: fleetManager.outputs.pendingApprovalAlertPrincipalId
    enableFleet: true
  }
}

module containerInsights './modules/azmonitor/container-insights.bicep' = if (enableContainerInsights) {
  name: 'containerInsights'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    aksClusterResourceId: aksCluster.outputs.aksId
    logAnalyticsWorkspaceId: azmonitorCore.outputs.logAnalyticsId
    nameSuffix: '${appName}-${environment}'
    enableContainerNetworkLogs: enableContainerNetworkLogs
    dataCollectionPreset: containerInsightsPreset
    streams: containerInsightsStreams
  }
}

module aksDiagnostics './modules/azmonitor/aks-diagnostics.bicep' = if (enableAksDiagnostics) {
  name: 'aksDiagnostics'
  scope: resourceGroup
  params: {
    aksClusterName: aksCluster.outputs.aksNameOut
    logAnalyticsWorkspaceId: azmonitorCore.outputs.logAnalyticsId
    logAnalyticsWorkspaceName: azmonitorCore.outputs.logAnalyticsNameOut
  }
}

module otlpDcra './modules/azmonitor/otlp-dcra.bicep' = {
  name: 'otlpDcra'
  scope: resourceGroup
  params: {
    aksClusterResourceId: aksCluster.outputs.aksId
    dataCollectionRuleId: azmonitorCore.outputs.applicationInsightsDataCollectionRuleId
  }
}

module redisEnterprise './modules/redis.bicep' = {
  name: 'redis'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    redisName: redisEnterpriseName
    vnetId: network.outputs.vnetId
    privateEndpointSubnetId: network.outputs.peSubnetId
    principalObjectId: chaosAppIdentity.outputs.principalId
  }
}

module azureMonitorSliMembers './modules/azmonitor/service-group-members.bicep' = if (enableAzureMonitorSliEffective) {
  name: 'azureMonitorSliMembers'
  scope: resourceGroup
  dependsOn: [
    aksCluster
    azmonitorCore
    containerRegistry
    redisEnterprise
    azureMonitorSliServiceGroup
  ]
  params: {
    serviceGroupId: azureMonitorSliEffectiveServiceGroupId
    aksName: aksClusterName
    prometheusWorkspaceName: azureMonitorWorkspaceName
    appAzureMonitorWorkspaceName: appAzureMonitorWorkspaceName
    logAnalyticsWorkspaceName: logAnalyticsWorkspaceName
    applicationInsightsName: applicationInsightsName
    redisName: redisEnterpriseName
    containerRegistryName: containerRegistryName
  }
}

module chaosAppIdentity './modules/identity.bicep' = {
  name: 'chaosAppIdentity'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    identityName: chaosAppIdentityName
    oidcIssuerUrl: aksCluster.outputs.oidcIssuerUrl
    serviceAccountNamespace: serviceAccountNamespace
    serviceAccountName: serviceAccountName
  }
}

module chaosExperiments './modules/chaos/experiments.bicep' = if (enableChaosExperiments) {
  name: 'chaosExperiments'
  scope: resourceGroup
  params: {
    location: location
    tags: tags
    aksId: aksCluster.outputs.aksId
    namespace: chaosNamespace
    appLabel: chaosAppLabel
    defaultDuration: chaosDuration
    // Pass as array; when omitted, experiments module falls back to *.*.redis.azure.net
    redisHosts: [redisEnterprise.outputs.redisHost]
  }
}

// Chaos experiment role assignments - separated into a module so principalId
// (a runtime value from SystemAssigned identity) can be passed as a parameter,
// making it a deploy-time value usable in guid() for role assignment names.
module chaosRoleAssignments './modules/chaos/role-assignments.bicep' = if (enableChaosExperiments) {
  name: 'chaosRoleAssignments'
  scope: resourceGroup
  params: {
    aksId: aksCluster.outputs.aksId
    podChaosPrincipalId: chaosExperiments!.outputs.podChaosPrincipalId
    networkChaosPrincipalId: chaosExperiments!.outputs.networkChaosPrincipalId
    networkChaosLossPrincipalId: chaosExperiments!.outputs.networkChaosLossPrincipalId
    stressChaosPrincipalId: chaosExperiments!.outputs.stressChaosPrincipalId
    ioChaosPrincipalId: chaosExperiments!.outputs.ioChaosPrincipalId
    timeChaosPrincipalId: chaosExperiments!.outputs.timeChaosPrincipalId
    kernelChaosPrincipalId: chaosExperiments!.outputs.kernelChaosPrincipalId
    httpChaosPrincipalId: chaosExperiments!.outputs.httpChaosPrincipalId
    dnsChaosPrincipalId: chaosExperiments!.outputs.dnsChaosPrincipalId
  }
}

@description('AKS cluster name')
output AZURE_AKS_CLUSTER_NAME string = aksCluster.outputs.aksNameOut

@description('ACR endpoint for azd to push images')
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.outputs.loginServer

@description('App Insights connection string')
output APPLICATIONINSIGHTS_CONNECTION_STRING string = azmonitorCore.outputs.applicationInsightsConnectionString

@description('Application Insights resource name')
output AZURE_APPLICATIONINSIGHTS_NAME string = applicationInsightsName

@description('Log Analytics workspace id')
output AZURE_LOG_ANALYTICS_ID string = azmonitorCore.outputs.logAnalyticsId
@description('Log Analytics workspace name')
output AZURE_LOG_ANALYTICS_WORKSPACE_NAME string = azmonitorCore.outputs.logAnalyticsNameOut

@description('Azure Monitor Workspace resource ID for Managed Prometheus')
output AZURE_MONITOR_PROMETHEUS_WORKSPACE_ID string = prometheusWorkspaceResourceId

@description('Azure Managed Redis resource id')
output AZURE_REDIS_ID string = redisEnterprise.outputs.redisId

@description('Azure Managed Redis hostname')
output AZURE_REDIS_HOST string = redisEnterprise.outputs.redisHost

@description('Azure Managed Redis TLS port')
output AZURE_REDIS_PORT int = redisEnterprise.outputs.redisPort
@description('Chaos Lab App Managed Identity client id for Workload Identity')
output AZURE_CHAOS_APP_IDENTITY_CLIENT_ID string = chaosAppIdentity.outputs.clientId

@description('AKS cluster identity principal ID')
output AZURE_AKS_IDENTITY_PRINCIPAL_ID string = aksCluster.outputs.aksIdentityPrincipalId

@description('Static Public IP address for App Routing (Gateway API)')
output AZURE_INGRESS_PUBLIC_IP string = network.outputs.publicIPAddress

@description('Public IP resource ID for App Routing (Gateway API)')
output AZURE_INGRESS_PUBLIC_IP_ID string = network.outputs.publicIPId

@description('FQDN for ingress public IP')
output AZURE_INGRESS_FQDN string = network.outputs.fqdn

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = resourceGroup.name
@description('Public IP resource name for App Routing (Gateway API)')
output AZURE_INGRESS_PUBLIC_IP_NAME string = network.outputs.publicIPName

@description('Azure tenant ID')
output AZURE_TENANT_ID string = tenant().tenantId

@description('Azure Service Group resource ID used by Azure Monitor SLI')
output AZURE_MONITOR_SLI_SERVICE_GROUP_ID string = enableAzureMonitorSliEffective ? azureMonitorSliEffectiveServiceGroupId : ''

@description('Azure Service Group name used by Azure Monitor SLI')
output AZURE_MONITOR_SLI_SERVICE_GROUP_NAME string = enableAzureMonitorSliEffective ? azureMonitorSliEffectiveServiceGroupName : ''

@description('Azure Monitor Availability SLI resource name')
output AZURE_MONITOR_AVAILABILITY_SLI_NAME string = enableAzureMonitorSliEffective ? azureMonitorAvailabilitySliName : ''

@description('Azure Monitor Latency SLI resource name')
output AZURE_MONITOR_LATENCY_SLI_NAME string = enableAzureMonitorSliEffective ? azureMonitorLatencySliName : ''

@description('Azure Monitor SLI managed identity resource ID')
#disable-next-line BCP318
output AZURE_MONITOR_SLI_IDENTITY_ID string = enableAzureMonitorSliEffective ? azureMonitorSliIdentity.outputs.identityId : ''

@description('Azure Monitor SLI managed identity client ID')
#disable-next-line BCP318
output AZURE_MONITOR_SLI_IDENTITY_CLIENT_ID string = enableAzureMonitorSliEffective ? azureMonitorSliIdentity.outputs.clientId : ''
