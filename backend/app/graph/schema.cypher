// Applied by app.ingestion.init_schema. `{{EMBEDDING_DIMENSIONS}}` is templated from settings.

CREATE CONSTRAINT plant_id IF NOT EXISTS FOR (n:Plant) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT production_line_id IF NOT EXISTS FOR (n:ProductionLine) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT machine_model_id IF NOT EXISTS FOR (n:MachineModel) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT machine_id IF NOT EXISTS FOR (n:Machine) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT component_id IF NOT EXISTS FOR (n:Component) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT sensor_id IF NOT EXISTS FOR (n:Sensor) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT error_type_id IF NOT EXISTS FOR (n:ErrorType) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT error_event_id IF NOT EXISTS FOR (n:ErrorEvent) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT maintenance_event_id IF NOT EXISTS FOR (n:MaintenanceEvent) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT failure_id IF NOT EXISTS FOR (n:Failure) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT alarm_id IF NOT EXISTS FOR (n:Alarm) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT technician_id IF NOT EXISTS FOR (n:Technician) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE;

CREATE INDEX machine_status IF NOT EXISTS FOR (n:Machine) ON (n.status);
CREATE INDEX alarm_status IF NOT EXISTS FOR (n:Alarm) ON (n.status);
CREATE INDEX failure_ts IF NOT EXISTS FOR (n:Failure) ON (n.ts);

CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (c:Chunk) ON c.embedding
  OPTIONS { indexConfig: { `vector.dimensions`: {{EMBEDDING_DIMENSIONS}}, `vector.similarity_function`: 'cosine' } };
CREATE VECTOR INDEX failure_embedding IF NOT EXISTS FOR (f:Failure) ON f.embedding
  OPTIONS { indexConfig: { `vector.dimensions`: {{EMBEDDING_DIMENSIONS}}, `vector.similarity_function`: 'cosine' } };
CREATE FULLTEXT INDEX chunk_fulltext IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text];
