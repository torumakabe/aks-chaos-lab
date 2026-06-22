@description('Deployment location')
param location string
@description('AKS name')
param aksName string
@description('Tags object')
param tags object = {}
@description('Kubernetes version (x.y or x.y.z).')
param kubernetesVersion string = '1.34'
@description('Node resource group name for AKS managed resources')
param nodeResourceGroupName string
@description('Node VM size')
param nodeVmSize string
@description('AKS subnet id')
param aksSubnetId string
@description('AKS API Server subnet id')
param aksApiSubnetId string = ''
@description('Log Analytics workspace resource ID for Container Insights')
param logAnalyticsWorkspaceId string

@description('Enable container network logs collection (ACNS + Cilium required)')
param enableContainerNetworkLogs bool = true

@description('Action Group resource ID for alerts (optional, leave empty for lab use)')
param actionGroupId string = ''

@description('AKS SKU mode. Only "Base" is supported (see ADR-010).')
@allowed([
  'Base'
])
param skuName string = 'Base'

var resourceGroupSuffix = uniqueString(resourceGroup().id)

// KQL queries for auto-upgrade alerts
var aksNodeOsAutoUpgradeKqlTemplate = sys.loadTextContent('templates/aks-nodeos-autoupgrade.kql')
var aksNodeOsAutoUpgradeKql = replace(aksNodeOsAutoUpgradeKqlTemplate, '{{AKS_ID}}', aksCluster.id)

// AKS cluster properties common to identity, monitoring, and ingress configuration.
var aksCommonProperties = {
  nodeResourceGroup: nodeResourceGroupName
  dnsPrefix: 'dns${substring(resourceGroupSuffix, 0, 8)}'
  metricsProfile: {
    costAnalysis: { enabled: true }
  }
  workloadAutoScalerProfile: {
    verticalPodAutoscaler: {
      enabled: true
      addonAutoscaling: 'Enabled'
    }
  }
  // Enable Azure Monitor managed Prometheus (metrics) and container insights
  azureMonitorProfile: {
    containerInsights: {
      enabled: true
      logAnalyticsWorkspaceResourceId: logAnalyticsWorkspaceId
      #disable-next-line BCP081
      containerNetworkLogs: enableContainerNetworkLogs ? 'Enabled' : null
    }
    metrics: {
      enabled: true
      kubeStateMetrics: {
        metricAnnotationsAllowList: ''
        metricLabelsAllowlist: ''
      }
    }
    appMonitoring: {
      // autoInstrumentation.enabled installs the Instrumentation CRD operator and webhook.
      // This does NOT auto-inject SDKs — that is controlled by autoInstrumentationPlatforms
      // in the Instrumentation CR (set to [] for auto-configuration mode).
      // The chaos-app uses manual OTel SDK instrumentation with OTLP exporter.
      // AKS Auto-Configuration injects OTEL_EXPORTER_OTLP_ENDPOINT into annotated pods.
      autoInstrumentation: {
        enabled: true
      }
      openTelemetryLogsAndTraces: {
        enabled: true
      }
      openTelemetryMetrics: {
        enabled: true
      }
    }
  }
  // Gateway API (App Routing Istio) ingress configuration as the successor to managed NGINX (sunset 2026/11)
  ingressProfile: {
    gatewayAPI: {
      installation: 'Standard'
    }
    webAppRouting: {
      enabled: true
      nginx: {
        defaultIngressControllerType: 'None'
      }
      gatewayAPIImplementations: {
        appRoutingIstio: {
          mode: 'Enabled'
        }
      }
    }
  }
  // Azure Policy add-on (Gatekeeper). Declared here so IaC owns the desired state and
  // the subscription-scope Defender for Cloud DINE policy ("Deploy Azure Policy Add-on
  // to AKS") finds its existence condition satisfied, stopping its recurring cluster PUT
  // (which previously left the cluster in provisioningState=Failed). See ADR-015.
  addonProfiles: {
    azurepolicy: {
      enabled: true
    }
  }
}

// Base mode specific properties
// Provides full control over Kubernetes version, node pools, and networking
var aksBaseSpecificProperties = {
  // Allow explicit Kubernetes version control
  kubernetesVersion: kubernetesVersion
  oidcIssuerProfile: { enabled: true }
  securityProfile: { workloadIdentity: { enabled: true } }
  // Enable Azure RBAC for Kubernetes authorization
  aadProfile: {
    managed: true
    enableAzureRbac: true
  }
  // Disable local accounts to enforce Azure AD/Entra ID-only authentication
  disableLocalAccounts: true
  // Enable API Server VNet Integration for Base mode
  apiServerAccessProfile: {
    enableVnetIntegration: true
    subnetId: aksApiSubnetId
  }
  // Auto-upgrade settings - only NodeImage for node OS upgrades
  autoUpgradeProfile: {
    nodeOSUpgradeChannel: 'NodeImage'
  }
  autoScalerProfile: {
    'daemonset-eviction-for-occupied-nodes': true
    'max-node-provision-time': '15m'
    'scale-down-delay-after-add': '10m'
  }
  networkProfile: {
    // Azure CNI overlay with Cilium dataplane
    networkPlugin: 'azure'
    networkPluginMode: 'overlay'
    networkDataplane: 'cilium'
    networkPolicy: 'cilium'
    advancedNetworking: {
      enabled: true
      observability: {
        enabled: true
      }
      security: {
        enabled: true
        advancedNetworkPolicies: 'L7'
      }
    }
  }
  agentPoolProfiles: [
    {
      name: 'default'
      vmSize: nodeVmSize
      mode: 'System'
      // Pin node OS to Ubuntu 24.04 ahead of Ubuntu 22.04 retirement (ADR-008)
      osSKU: 'Ubuntu2404'
      // Distribute nodes across availability zones (1/2/3 if region supports)
      availabilityZones: [
        '1'
        '2'
        '3'
      ]
      vnetSubnetID: aksSubnetId
      orchestratorVersion: kubernetesVersion
      enableAutoScaling: true
      minCount: 1
      maxCount: 3
      // Blue-green upgrade strategy for safer node OS auto-upgrades.
      upgradeStrategy: 'BlueGreen'
      upgradeSettingsBlueGreen: {
        drainBatchSize: '50%'
        drainTimeoutInMinutes: 30
        batchSoakDurationInMinutes: 15
        finalSoakDurationInMinutes: 60
      }
    }
  ]
}

// Compose final properties from common + Base-specific configurations
var aksBaseProperties = union(aksCommonProperties, aksBaseSpecificProperties)

// User Assigned Managed Identity for AKS cluster
resource aksIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${aksName}'
  location: location
  tags: tags
}

#disable-next-line BCP081
resource aksCluster 'Microsoft.ContainerService/managedClusters@2026-03-02-preview' = {
  name: aksName
  location: location
  tags: tags
  // Enable Uptime SLA by switching AKS SKU tier
  sku: {
    name: skuName
    tier: 'Standard'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${aksIdentity.id}': {}
    }
  }
  properties: aksBaseProperties
  dependsOn: [
    aksSubnetNetworkContributorRole
  ]
}

// Assign Network Contributor on the AKS subnet to the cluster's managed identity
// Parse subnet and vnet names from the provided subnet resource ID
var subnetIdParts = split(aksSubnetId, '/')
var subnetName = last(subnetIdParts)
var virtualNetworkName = subnetIdParts[8]

@description('Existing VNet (parent of the AKS subnet)')
resource existingVirtualNetwork 'Microsoft.Network/virtualNetworks@2024-07-01' existing = {
  name: virtualNetworkName
}

@description('Existing AKS subnet (role assignment scope)')
resource existingAksSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-07-01' existing = {
  name: subnetName
  parent: existingVirtualNetwork
}

// Network Contributor role definition ID
var networkContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4d97b98b-1d4f-4787-a291-c67834d212e7'
)

resource aksSubnetNetworkContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(existingAksSubnet.id, 'NetworkContributor', aksIdentity.name)
  scope: existingAksSubnet
  properties: {
    roleDefinitionId: networkContributorRoleId
    principalId: aksIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Assign Network Contributor on the resource group for managing public IPs
resource aksResourceGroupNetworkContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, 'NetworkContributor', aksIdentity.name)
  properties: {
    roleDefinitionId: networkContributorRoleId
    principalId: aksIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Output AKS cluster identity principal ID for additional role assignments
output aksIdentityPrincipalId string = aksIdentity.properties.principalId

// AKS Managed Node OS Upgrade Schedule (weekly on Wednesday)
#disable-next-line BCP081
resource aksMaintenanceNodeConf 'Microsoft.ContainerService/managedClusters/maintenanceConfigurations@2026-03-01' = {
  parent: aksCluster
  name: 'aksManagedNodeOSUpgradeSchedule'
  properties: {
    maintenanceWindow: {
      durationHours: 4
      schedule: {
        weekly: {
          dayOfWeek: 'Wednesday'
          intervalWeeks: 1
        }
      }
      startDate: '2025-04-01'
      startTime: '00:00'
      utcOffset: '+09:00'
    }
  }
}

// Node OS auto-upgrade alert (native resource)
resource aksNodeOSAutoUpgradeAlertRule 'Microsoft.Insights/scheduledQueryRules@2026-03-01' = {
  name: 'aks-nodeos-autoupgrade'
  location: location
  tags: tags
  kind: 'LogAlert'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'aks-nodeos-autoupgrade'
    description: 'AKS Node OS auto-upgrade detected via ARG events'
    enabled: true
    scopes: [aksCluster.id]
    evaluationFrequency: 'PT30M'
    windowSize: 'PT60M'
    severity: 3
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: aksNodeOsAutoUpgradeKql
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          metricMeasureColumn: ''
          dimensions: [
            {
              name: 'status'
              operator: 'Include'
              values: ['*']
            }
          ]
        }
      ]
    }
    actions: {
      actionGroups: actionGroupId != '' ? [actionGroupId] : []
    }
  }
}

output aksId string = aksCluster.id
output aksNameOut string = aksCluster.name
output kubeletObjectId string = aksCluster.properties.identityProfile.kubeletidentity.objectId
output oidcIssuerUrl string = aksCluster.properties.oidcIssuerProfile.issuerURL
output nodeOsAutoUpgradeAlertPrincipalId string = aksNodeOSAutoUpgradeAlertRule.identity.principalId
