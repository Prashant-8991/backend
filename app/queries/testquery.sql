create or REPLACE VIEW gen_view as 
WITH RECURSIVE generation_cte AS (

    -- Generation 1: Root animals
    SELECT
        name,
        tag_number,
        mother_tag_number,
        mother_name,
        father_tag_number,
        father_name,
        1 AS gen
    FROM cattle_data
    WHERE mother_tag_number IS NULL

    UNION ALL

    -- Generation 2, 3, 4, ...
    SELECT
        cd.name,
        cd.tag_number,
        cd.mother_tag_number,
        cd.mother_name,
        cd.father_tag_number,
        cd.father_name,
        gc.gen + 1 AS gen
    FROM generation_cte AS gc
    INNER JOIN cattle_data AS cd
        ON cd.mother_tag_number = gc.tag_number
)

SELECT *
FROM generation_cte
ORDER BY gen, tag_number;



SELECT
    father.name AS father_name,
    father.tag_number AS father_tag_number,
    child.name AS child_name,
    child.tag_number AS child_tag_number
FROM cattle_data AS father
JOIN cattle_data AS child
    ON child.father_tag_number = father.tag_number
WHERE father.animal_type = 'BULL'
ORDER BY father.tag_number, child.tag_number;



select name, tag_number, animal_type from cattle_data WHERE animal_type = 'BULL';


-- select cd.father_tag_number, cd.father_name, cd.name, cd.tag_number, gv.gen as generation from cattle_data cd
-- join gen_view gv ON gv.tag_number = cd.tag_number
-- WHERE cd.tag_number in (
--     select tag_number from cattle_data WHERE animal_type = 'BULL'
-- );


select name as "Father Name", tag_number as "Father Tag Number" from cattle_data cd where cd.animal_type = 'BULL';

select name, tag_number, father_name from cattle_data WHERE animal_type = 'BULL';

select name, tag_number, father_name from cattle_data WHERE father_tag_number = 'SOM-333';

select * from cattle_data WHERE father_name = 'શ્યામ';


select * from cattle_data cd WHERE animal_type = 'BULL';

select name, tag_number, father_name, father_tag_number from cattle_data cd where cd.father_tag_number in (
    select tag_number from cattle_data WHERE cattle_data.animal_type = 'BULL'
);


select count(*) - 257 - 36  from cattle_data;


select count(*) FROM cattle_data WHERE father_tag_number is NULL;


select count(*) FROM cattle_data WHERE acquisition_type = 'PURCHASED';

select distinct  FROM cattle_data;



select * from cattle_data cd WHERE cd.father_tag_number in (
    select tag_number FROM cattle_data WHERE acquisition_type = 'BIRTH'
);

select * from cattle_data WHERE name = 'લીલડો';


select age(date_of_birth::date) FROM cattle_data WHERE name = 'લીલડો';


select DISTINCT gender from cattle_data;


SELECT name, tag_number, acquisition_type, animal_type, date_of_birth, age(date_of_birth::date)
FROM cattle_data 
WHERE age(date_of_birth::date) > INTERVAL '5 years' AND date_of_birth != '-' AND gender = 'Male';


select DISTINCT father_tag_number from cattle_data;


select * FROM cattle_data cd
left JOIN cattle_data c ON c.id = cd.id
WHERE cd.father_tag_number in (
    select DISTINCT father_tag_number from cattle_data 
);