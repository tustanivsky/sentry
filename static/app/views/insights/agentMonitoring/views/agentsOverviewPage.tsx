import React from 'react';
import styled from '@emotion/styled';

import {SegmentedControl} from 'sentry/components/core/segmentedControl';
import * as Layout from 'sentry/components/layouts/thirds';
import {DatePageFilter} from 'sentry/components/organizations/datePageFilter';
import {EnvironmentPageFilter} from 'sentry/components/organizations/environmentPageFilter';
import PageFilterBar from 'sentry/components/organizations/pageFilterBar';
import {ProjectPageFilter} from 'sentry/components/organizations/projectPageFilter';
import TransactionNameSearchBar from 'sentry/components/performance/searchBar';
import {t} from 'sentry/locale';
import {space} from 'sentry/styles/space';
import {useLocation} from 'sentry/utils/useLocation';
import useOrganization from 'sentry/utils/useOrganization';
import {Widget} from 'sentry/views/dashboards/widgets/widget/widget';
import {limitMaxPickableDays} from 'sentry/views/explore/utils';
import {ModelsTable} from 'sentry/views/insights/agentMonitoring/components/modelsTable';
import {ToolsTable} from 'sentry/views/insights/agentMonitoring/components/toolsTable';
import {TracesTable} from 'sentry/views/insights/agentMonitoring/components/tracesTable';
import {
  TableType,
  useActiveTable,
} from 'sentry/views/insights/agentMonitoring/hooks/useActiveTable';
import {AgentInsightsFeature} from 'sentry/views/insights/agentMonitoring/utils/features';
import * as ModuleLayout from 'sentry/views/insights/common/components/moduleLayout';
import {ModulePageProviders} from 'sentry/views/insights/common/components/modulePageProviders';
import {ModuleBodyUpsellHook} from 'sentry/views/insights/common/components/moduleUpsellHookWrapper';
import {ToolRibbon} from 'sentry/views/insights/common/components/ribbon';
import {useOnboardingProject} from 'sentry/views/insights/common/queries/useOnboardingProject';
import {AgentsPageHeader} from 'sentry/views/insights/pages/agents/agentsPageHeader';
import {WidgetGrid} from 'sentry/views/insights/pages/platform/shared/styles';
import {useTransactionNameQuery} from 'sentry/views/insights/pages/platform/shared/useTransactionNameQuery';
import {ModuleName} from 'sentry/views/insights/types';
import {getTransactionSearchQuery} from 'sentry/views/performance/utils';

const TableControl = SegmentedControl<TableType>;
const TableControlItem = SegmentedControl.Item<TableType>;

function AgentsMonitoringPage() {
  const location = useLocation();
  const organization = useOrganization();
  const onboardingProject = useOnboardingProject();
  const datePageFilterProps = limitMaxPickableDays(organization);

  const showOnboarding = onboardingProject !== undefined;

  const {eventView, handleSearch} = useTransactionNameQuery();
  const searchBarQuery = getTransactionSearchQuery(location, eventView.query);

  const {activeTable, onActiveTableChange} = useActiveTable();

  return (
    <React.Fragment>
      <AgentsPageHeader module={ModuleName.AGENTS} />
      <ModuleBodyUpsellHook moduleName={ModuleName.AGENTS}>
        <Layout.Body>
          <Layout.Main fullWidth>
            <ModuleLayout.Layout>
              <ModuleLayout.Full>
                <ToolRibbon>
                  <PageFilterBar condensed>
                    <ProjectPageFilter resetParamsOnChange={['starred']} />
                    <EnvironmentPageFilter />
                    <DatePageFilter {...datePageFilterProps} />
                  </PageFilterBar>
                  {!showOnboarding && (
                    <StyledTransactionNameSearchBar
                      organization={organization}
                      eventView={eventView}
                      onSearch={handleSearch}
                      query={searchBarQuery}
                    />
                  )}
                </ToolRibbon>
              </ModuleLayout.Full>
              <ModuleLayout.Full>
                <WidgetGrid>
                  <WidgetGrid.Position1>
                    <PlaceholderWidget title="Traffic" />
                  </WidgetGrid.Position1>
                  <WidgetGrid.Position2>
                    <PlaceholderWidget title="Duration" />
                  </WidgetGrid.Position2>
                  <WidgetGrid.Position3>
                    <PlaceholderWidget title="Issues" />
                  </WidgetGrid.Position3>
                  <WidgetGrid.Position4>
                    <PlaceholderWidget title="LLM Generations" />
                  </WidgetGrid.Position4>
                  <WidgetGrid.Position5>
                    <PlaceholderWidget title="Tool Usage" />
                  </WidgetGrid.Position5>
                  <WidgetGrid.Position6>
                    <PlaceholderWidget title="Token Usage" />
                  </WidgetGrid.Position6>
                </WidgetGrid>
                <ControlsWrapper>
                  <TableControl
                    value={activeTable}
                    onChange={onActiveTableChange}
                    size="sm"
                  >
                    <TableControlItem key={TableType.TRACES}>
                      {t('Traces')}
                    </TableControlItem>
                    <TableControlItem key={TableType.MODELS}>
                      {t('Models')}
                    </TableControlItem>
                    <TableControlItem key={TableType.TOOLS}>
                      {t('Tools')}
                    </TableControlItem>
                  </TableControl>
                </ControlsWrapper>
                {activeTable === TableType.TRACES && <TracesTable />}
                {activeTable === TableType.MODELS && <ModelsTable />}
                {activeTable === TableType.TOOLS && <ToolsTable />}
              </ModuleLayout.Full>
            </ModuleLayout.Layout>
          </Layout.Main>
        </Layout.Body>
      </ModuleBodyUpsellHook>
    </React.Fragment>
  );
}

function PageWithProviders() {
  return (
    <AgentInsightsFeature>
      <ModulePageProviders
        moduleName={ModuleName.AGENTS}
        analyticEventName="insight.page_loads.agents"
      >
        <AgentsMonitoringPage />
      </ModulePageProviders>
    </AgentInsightsFeature>
  );
}

const StyledTransactionNameSearchBar = styled(TransactionNameSearchBar)`
  flex: 2;
`;

function PlaceholderWidget({title}: {title?: string}) {
  return (
    <Widget
      Title={<Widget.WidgetTitle title={title ?? 'Placeholder'} />}
      Visualization={null}
    />
  );
}

const ControlsWrapper = styled('div')`
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: ${space(1)};
  margin: ${space(2)} 0;
`;

export default PageWithProviders;
